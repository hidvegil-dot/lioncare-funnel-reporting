const { ff } = require('./fireflies');

const COACH_PROMPT = `Te a LionCare pénzügyi tanácsadó élő meeting-copilotja vagy. A beszélgetés eddigi tartalma alapján segítsd a tanácsadót a következő legjobb lépéssel.

Elvek:
- Ne termékkel kezdj; előbb helyzet, cél, következmény, prioritás, majd megoldás.
- Az ügyfél saját érve erősebb. Elsősorban kérdést javasolj, ne hosszú érvelést.
- Használd vissza az ügyfél saját erős szavait.
- Egy kérdés egyszerre.
- Ne nyomulj, ne ijesztgess, ne találj ki adatot.
- Ha az ügyfél azt mondja, hogy átgondolja, ne adj több érvet: tárd fel, mit kell még átgondolnia.
- Ha a valódi akadály tiszta, arra fókuszálj.
- Ha döntésközeli, ne magyarázz tovább: kérj konkrét következő lépést.

Csak az alábbi formában válaszolj, magyarul, tömören:
HELYZET: <1 rövid mondat>
KÖVETKEZŐ KÉRDÉS: <1 konkrét, magázó kérdés idézőjelben>
MIRE FIGYELJ: <1 rövid mondat>
ÜGYFÉL SAJÁT SZAVAI: <legfeljebb 2 fontos rövid kifejezés, vagy —>
MEETING SZAKASZ: <Nyitás|Helyzetfeltárás|Cél|Következmény|Prioritás|Megoldás|Döntési akadály|Zárás>
ZÁRÁSI KÉSZÜLTSÉG: <0-100 egész szám>`;

module.exports = async (req, res) => {
  if (req.method !== 'POST') return res.status(405).json({ ok:false, error:'POST szükséges' });
  const id = req.body?.id;
  if (!id) return res.status(400).json({ ok:false, error:'Hiányzó meeting ID' });

  try {
    const transcriptData = await ff(`query Transcript($id: String!) {
      transcript(id: $id) {
        id title
        sentences { speaker_name text start_time }
      }
    }`, { id });

    const sentences = transcriptData.transcript?.sentences || [];
    const conversation = sentences.slice(-80).map(s => `${s.speaker_name || 'Beszélő'}: ${s.text || ''}`).join('\n');

    if (!conversation.trim()) {
      return res.status(200).json({ ok:true, result:{ answer:'HELYZET: Még nincs elég beszélgetési adat.\nKÖVETKEZŐ KÉRDÉS: „Mi az, ami miatt most érdemesnek látta, hogy beszéljünk erről?”\nMIRE FIGYELJ: Ne menj még termékre.\nÜGYFÉL SAJÁT SZAVAI: —\nMEETING SZAKASZ: Nyitás\nZÁRÁSI KÉSZÜLTSÉG: 10' } });
    }

    // V1: a mélyebb elemzést egyelőre a Fireflies transcriptre épített szabálymotor végzi a frontenden.
    // Ezt a végpontot azért tartjuk meg, hogy később külön AI modellre lehessen kötni anélkül,
    // hogy a frontend szerkezetét újra kellene írni.
    const recent = conversation.toLowerCase();
    let answer = 'HELYZET: A beszélgetés feltárási szakaszban van.\nKÖVETKEZŐ KÉRDÉS: „Mi az, ami Önnek ebben a legfontosabb?”\nMIRE FIGYELJ: Egy kérdés egyszerre.\nÜGYFÉL SAJÁT SZAVAI: —\nMEETING SZAKASZ: Helyzetfeltárás\nZÁRÁSI KÉSZÜLTSÉG: 35';

    if (/átgondol|gondolkod|meggondol/.test(recent)) {
      answer = 'HELYZET: Az ügyfél halasztó mondatot használt; a valódi bizonytalanság még nem tiszta.\nKÖVETKEZŐ KÉRDÉS: „Mi az, amit még át kell gondolnia ahhoz, hogy el tudja dönteni, megfelelő-e Önnek?”\nMIRE FIGYELJ: Ne adj több érvet. Előbb tárd fel a valódi okot.\nÜGYFÉL SAJÁT SZAVAI: átgondolom\nMEETING SZAKASZ: Döntési akadály\nZÁRÁSI KÉSZÜLTSÉG: 65';
    } else if (/drága|sok pénz|havi teher|nem fér bele/.test(recent)) {
      answer = 'HELYZET: A havi vállalás lett a fő döntési szempont.\nKÖVETKEZŐ KÉRDÉS: „Mi az a havi szint, amit hosszú távon kényelmesen tudna tartani?”\nMIRE FIGYELJ: Ne védd az árat; előbb tisztázd a keretet.\nÜGYFÉL SAJÁT SZAVAI: havi teher\nMEETING SZAKASZ: Prioritás\nZÁRÁSI KÉSZÜLTSÉG: 58';
    } else if (/biztonság|kiszolgáltat|gyerek|család/.test(recent)) {
      answer = 'HELYZET: Erős személyes motiváció jelent meg.\nKÖVETKEZŐ KÉRDÉS: „Mit jelentene Önnek konkrétan az, hogy ebben biztonságban legyen?”\nMIRE FIGYELJ: Használd vissza az ügyfél saját szavait.\nÜGYFÉL SAJÁT SZAVAI: biztonság / kiszolgáltatottság\nMEETING SZAKASZ: Cél\nZÁRÁSI KÉSZÜLTSÉG: 48';
    }

    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json({ ok:true, result:{ answer } });
  } catch (e) {
    return res.status(500).json({ ok:false, error:e.message });
  }
};
