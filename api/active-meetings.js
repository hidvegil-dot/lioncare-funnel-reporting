const { ff } = require('./fireflies');

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');

  if (req.method !== 'GET') {
    return res.status(405).json({ ok: false, error: 'GET szükséges' });
  }

  try {
    // Elsődleges lekérés: csak ténylegesen aktív meetingek, a Fireflies jelenlegi sémája szerint.
    let data;
    try {
      data = await ff(`query ActiveMeetings {
        active_meetings(input: { states: [active] }) {
          id
          title
          organizer_email
          meeting_link
          start_time
          state
        }
      }`);
    } catch (primaryError) {
      // Fallback régebbi / eltérő API-séma esetére.
      data = await ff(`query ActiveMeetings {
        active_meetings {
          id
          title
          organizer_email
          meeting_link
          start_time
          state
        }
      }`);
    }

    const meetings = Array.isArray(data?.active_meetings) ? data.active_meetings : [];
    return res.status(200).json({ ok: true, meetings });
  } catch (e) {
    const msg = String(e?.message || e || 'Ismeretlen Fireflies hiba');
    let code = 'FIREFLIES_ERROR';
    if (/FIREFLIES_API_KEY nincs beállítva/i.test(msg)) code = 'MISSING_API_KEY';
    else if (/unauthor|forbidden|token|api key|auth/i.test(msg)) code = 'AUTH_ERROR';
    else if (/rate|too_many_requests|429/i.test(msg)) code = 'RATE_LIMIT';
    else if (/elevated|privilege|permission/i.test(msg)) code = 'PERMISSION_ERROR';

    return res.status(500).json({
      ok: false,
      code,
      error: msg
    });
  }
};
