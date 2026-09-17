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
  for (const item of data?.output || []) for (const part of item?.content || []) if (typeof part?.text === 'string' && part.text) return part.text;
  return '';
}
function cleanJson(text) {
  let s = String(text || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '').trim();
  return JSON.parse(s);
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok:false, error:'POST szükséges' });
  const key = process.env.OPENAI_API_KEY;
  if (!key) return res.status(200).json({ ok:false, configured:false, error:'OPENAI_API_KEY nincs beállítva' });

  const body = req.body || {};
  const lines = Array.isArray(body.lines) ? body.lines.slice(-50) : [];
  if (!lines.length) return res.status(200).json({ ok:false, configured:true, error:'Nincs még elég transcript.' });

  const meetingType = body.meetingType || 'general';
  const meetingGoal = PLAYBOOKS[meetingType] || PLAYBOOKS.general;
  const previousQuestion = String(body.previousQuestion || '');
  const previousAnswerSummary = String(body.previousAnswerSummary || '');
  const depth = Math.max(0, Math.min(3, Number(body.depth || 0)));
  const mode = body.mode === 'awaiting_answer' ? 'awaiting_answer' : 'analyze_answer';
  const avoidQuestions = Array.isArray(body.avoidQuestions) ? body.avoidQuestions.slice(-8) : [];
  const transcript = lines.map((x, i) => `${i + 1}. ${x.speaker_name || 'Beszélő'}: ${x.text || ''}`).join('\n');

  const prompt = `Te a LionCare élő pénzügyi tárgyalási copilotja vagy. A tanácsadó neve Hidvégi László.\n\nMEETING KERET:\n${meetingGoal}\n\nA KÉRDEZÉSI MÓDSZER:\n- A cél nem az, hogy egymás után dobálj kérdéseket, hanem hogy kérdés -> ügyfél válasz -> rövid összegzés -> következő, mélyebb kérdés ciklusban dolgozz.\n- MINDIG várd meg az ügyfél érdemi válaszát az előző kérdésre.\n- Ha még nincs érdemi ügyfélválasz, ne generálj új kérdést: next_question legyen üres string, waiting_for_answer legyen true.\n- Ha van érdemi válasz, először foglald össze 1 mondatban, mit tudtunk meg valójában; utána adj EGY új kérdést, amely közvetlenül ebből a válaszból következik.\n- A kérdés ne legyen sablonos, ne ismételje az előzőt, és az ügyfél saját fontos szavait lehetőleg használja vissza.\n- Elsődleges logika: motiváció feltárása -> miért fontos -> mi történik ha nem változik -> prioritás -> csak ezután megoldás.\n- Törekedj 2-3 egymásra épülő mélyítő kérdésre, mielőtt magyarázatot vagy megoldást javasolsz.\n- Ha László magyarázni/oktatni kezd, miközben még van feltáratlan ügyfélgondolat, jelezd: inkább kérdezzen tovább.\n- Ne találj ki adatot és ne egészíts ki hiányzó ügyfélmotivációt.\n- Egy kérdés egyszerre, magázó forma.\n- Kifogásnál előbb a valódi okot tárd fel.\n\nAKTUÁLIS ÁLLAPOT:\nMód: ${mode}\nMélységi szint: ${depth}/3\nElőző ajánlott kérdés: ${previousQuestion || '—'}\nElőző válasz összegzése: ${previousAnswerSummary || '—'}\nKerülendő kérdések: ${avoidQuestions.length ? avoidQuestions.join(' | ') : '—'}\n\nLEGFRISSEBB TRANSCRIPT:\n${transcript}\n\nDöntsd el, hogy az ügyfél már adott-e érdemi választ az előző kérdésre. Az érdemi válasz új információt ad a motivációról, helyzetről, következményről, prioritásról, keretről vagy döntési akadályról; az olyan rövid reakció, mint „igen”, „értem”, „aha”, önmagában nem elég.\n\nCsak érvényes JSON-t adj vissza, markdown nélkül:\n{\n  "situation": "1 rövid mondat arról, mi történik most",\n  "stage": "Nyitás|Helyzetfeltárás|Cél|Következmény|Prioritás|Megoldás|Döntési akadály|Zárás",\n  "client_state": "1-3 szavas állapot",\n  "waiting_for_answer": true,\n  "answer_detected": false,\n  "answer_summary": "1 mondatos összegzés az ügyfél válaszáról, vagy —",\n  "next_question": "ha van érdemi válasz: 1 új, konkrét magázó kérdés idézőjelek nélkül; különben üres string",\n  "depth": 0,\n  "can_explain": false,\n  "watch": "1 rövid figyelmeztetés vagy fókusz",\n  "client_words": "legfeljebb 2 rövid, szó szerinti ügyfélkifejezés, vagy —",\n  "closing_readiness": 0,\n  "advisor_feedback": "rövid élő visszajelzés László kérdező/magyarázó működéséről",\n  "advisor_alert": false,\n  "reason_to_refresh": "mi változott"\n}\n\nSzabályok a mezőkhöz:\n- waiting_for_answer=true, ha még várni kell az ügyfél érdemi válaszára.\n- answer_detected=true csak akkor, ha tényleg megérkezett új érdemi ügyfélválasz.\n- depth: ha answer_detected=true és érdemes tovább mélyíteni, növeld legfeljebb 3-ig; ha témaváltás vagy új szál indul, lehet 1.\n- can_explain csak akkor true, ha legalább 2-3 releváns mélyítő válasz már megvan, vagy az ügyfél kifejezetten megoldást kér.\n- next_question legyen üres, ha waiting_for_answer=true.\n- closing_readiness 0-100 egész szám.`;

  try {
    const r = await fetch(OPENAI_URL, {
      method:'POST',
      headers:{ 'Content-Type':'application/json', 'Authorization':`Bearer ${key}` },
      body:JSON.stringify({
        model: process.env.OPENAI_COPILOT_MODEL || 'gpt-5.6-luna',
        input: prompt,
        reasoning: { effort: 'low' },
        max_output_tokens: 800
      })
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data?.error?.message || `OpenAI HTTP ${r.status}`);
    const result = cleanJson(extractOutputText(data));
    res.setHeader('Cache-Control','no-store');
    return res.status(200).json({ ok:true, configured:true, result, usage:data.usage || null });
  } catch (e) {
    return res.status(500).json({ ok:false, configured:true, error:e.message });
  }
};
