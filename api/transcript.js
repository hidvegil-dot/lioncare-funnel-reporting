const { ff } = require('./fireflies');

module.exports = async (req, res) => {
  const id = req.query?.id;
  if (!id) return res.status(400).json({ ok: false, error: 'Hiányzó meeting ID' });
  try {
    const data = await ff(`query Transcript($transcriptId: String!) {
      transcript(id: $transcriptId) {
        id
        title
        is_live
        organizer_email
        participants
        speakers { id name }
        sentences {
          index
          speaker_name
          speaker_id
          text
          raw_text
          start_time
          end_time
        }
      }
    }`, { transcriptId: id });
    res.setHeader('Cache-Control', 'no-store');
    res.status(200).json({ ok: true, transcript: data.transcript });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
};
