const OPENAI_URL = 'https://api.openai.com/v1/responses';

const PLAYBOOKS = {
  pension: 'Nyugdíj lead / első konzultáció. Cél: helyzet, motiváció, következmény, prioritás és vállalható keretek feltárása; ne termékkel kezdj.',
  needs: 'Igényfelmérés. Cél: jelenlegi helyzet, célok, kockázatok és prioritások feltárása; legyen világos, mire kell javaslatot készíteni.',
  proposal: 'Javaslati találkozó. Cél: a javaslat megértése, döntési feltételek és kifogások tisztázása, majd döntés vagy konkrét következő lépés.',
  followup: 'Utánkövetés / döntési hívás. Cél: a valódi döntési akadály azonosítása és lezárása.',
  review: 'Biztosítási felülvizsgálat. Cél: élethelyzet és meglévő védelem összevetése, hiányok és felesleges elemek azonosítása.',
  general: 'Általános pénzügyi konzultáció. Cél: a jelenlegi helyzet és a legfontosabb pénzügyi cél tisztázása, majd konkrét következő lépés.'
};

function extractOutputText(data) {
  if (typeof data?.output_text === 'string' && data.output_text) return data.output_text;
  for (const item of data?.output || []) {
    for (const part of item?.content || []) {
      if (typeof part?.text === 'string' && part.text) return part.text;
    }
  }
  return '';
}

function cleanJson(text) {
  let s = String(text || '').trim();
  s = s.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '').trim();
  return JSON.parse(s);
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok:false, error:'POST szükséges' });

  const key = process.env.OPENAI_API_KEY;
  if (!key) return res.status(200).json({ ok:false, configured:false, error:'OPENAI_API_KEY nincs beállítva' });

  const body = req.body || {};
  const lines = Array.isArray(body.lines) ? body.lines.slice(-40) : [];
  if (!lines.length) return res.status(200).json({ ok:false, configured:true, error:'Nincs még elég transcript.' });

  const meetingType = body.meetingType || 'general';
  const meetingGoal = PLAYBOOKS[meetingType] || PLAYBOOKS.general;
  const previousQuestion = String(body.previousQuestion || '');
  const avoidQuestions = Array.isArray(body.avoidQuestions) ? body.avoidQuestions.slice(-6) : [];

  const transcript = lines.map((x, i) => `${i + 1}. ${x.speaker_name || 'Beszélő'}: ${x.text || ''}`).join('\n');

  const prompt = `Te a LionCare élő pénzügyi tárgyalási copilotja vagy. A tanácsadó neve Hidvégi László.\n\nMEETING KERET:\n${meetingGoal}\n\nMŰKÖDÉSI ELVEK:\n- Az ügyfél saját érve erősebb, mint a tanácsadó magyarázata.\n- Elsősorban kérdéssel vezess: helyzet -> cél -> következmény -> prioritás -> megoldás -> döntési akadály -> zárás.\n- Egy kérdés egyszerre.\n- Használd vissza az ügyfél saját fontos szavait.\n- Ne találj ki adatot.\n- Ne nyomulj.\n- Ha kifogás vagy halasztás jelenik meg, előbb tárd fel a valódi okot; ne adj rögtön új érveket.\n- Ha döntésközeli a helyzet, ne magyarázz tovább, hanem segíts konkrét következő lépéshez jutni.\n- Figyeld a TANÁCSADÓ STÍLUSÁT is. Ha László túl sokat beszél, oktat, hosszú magyarázó blokkokat használ, nem kérdez vissza egy fontos ügyfélmondatra, vagy túl gyorsan megoldást ad, ezt röviden jelezd.\n- Ne kritizálj általánosan: csak olyan stílusjelzést adj, ami a mostani transcriptből ténylegesen látszik.\n- A következő kérdés közvetlenül a legfrissebb érdemi ügyfélgondolatra épüljön.\n- NE ismételd az előző vagy kerülendő kérdéseket.\n\nELŐZŐ AJÁNLOTT KÉRDÉS:\n${previousQuestion || '—'}\n\nKERÜLENDŐ KORÁBBI KÉRDÉSEK:\n${avoidQuestions.length ? avoidQuestions.join(' | ') : '—'}\n\nLEGFRISSEBB TRANSCRIPT:\n${transcript}\n\nCsak érvényes JSON-t adj vissza, markdown nélkül, pontosan ezekkel a mezőkkel:\n{\n  "situation": "1 rövid mondat arról, mi történik most",\n  "stage": "Nyitás|Helyzetfeltárás|Cél|Következmény|Prioritás|Megoldás|Döntési akadály|Zárás",\n  "client_state": "1-3 szavas állapot",\n  "next_question": "1 konkrét, magázó kérdés idézőjelek nélkül",\n  "watch": "1 rövid figyelmeztetés vagy fókusz",\n  "client_words": "legfeljebb 2 rövid, szó szerinti ügyfélkifejezés, vagy —",\n  "closing_readiness": 0,\n  "advisor_feedback": "rövid élő visszajelzés László kérdező/magyarázó működéséről",\n  "advisor_alert": false,\n  "reason_to_refresh": "mi változott, ami miatt ez az új javaslat releváns"\n}\nA closing_readiness 0-100 egész szám legyen. Az advisor_alert csak akkor true, ha most tényleg érdemes azonnal jelezni Lászlónak.`;

  try {
    const r = await fetch(OPENAI_URL, {
      method:'POST',
      headers:{ 'Content-Type':'application/json', 'Authorization':`Bearer ${key}` },
      body:JSON.stringify({
        model: process.env.OPENAI_COPILOT_MODEL || 'gpt-5.6-luna',
        input: prompt,
        reasoning: { effort: 'low' },
        max_output_tokens: 700
      })
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data?.error?.message || `OpenAI HTTP ${r.status}`);
    const raw = extractOutputText(data);
    const result = cleanJson(raw);
    res.setHeader('Cache-Control','no-store');
    return res.status(200).json({ ok:true, configured:true, result, usage:data.usage || null });
  } catch (e) {
    return res.status(500).json({ ok:false, configured:true, error:e.message });
  }
};
