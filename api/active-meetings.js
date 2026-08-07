const { ff } = require('./fireflies');

module.exports = async (req, res) => {
  try {
    const data = await ff(`query ActiveMeetings {
      active_meetings {
        id
        title
        organizer_email
        meeting_link
        start_time
        end_time
        privacy
        state
      }
    }`);
    res.setHeader('Cache-Control', 'no-store');
    res.status(200).json({ ok: true, meetings: data.active_meetings || [] });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
};
