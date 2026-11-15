# Voice input

[Documentation index](README.md)

## Dictate and edit

Select the microphone beside Send to dictate into the message draft. Text updates
as you speak; select Stop or press Escape to finish, then edit or send the draft.
Stop releases the microphone and unlocks the draft immediately. Any final words
arrive in the background; Send and the microphone button wait for transcription
to finish. Editing the draft cancels that wait, and late transcripts cannot
overwrite your edits. The microphone button floats while
listening, with a stationary raised state when reduced motion is preferred.
Existing text is preserved. Sending and editing are paused while recording, and
switching chats or signing out closes the microphone. Sessions stop after five
minutes or when the draft reaches 12,000 characters.

## Token and audio flow

Voice input uses Gemini Live dictation.
`GEMINI_API_KEY` (or `GOOGLE_API_KEY`) stays on the backend. The authenticated
`POST /api/voice/token` endpoint issues a single-use token that can open a session
within one minute and expires after five minutes. Its model and transcription
settings are fixed on the server. The browser streams 16 kHz PCM directly to
Gemini using that token; the app does not save recordings. Audio captured while
connecting stays in memory until the session opens. Start and Stop define one
turn, so pauses do not cut off later words. Final fragments are concatenated and
interim guesses replaced, so completed words remain in the draft.
The transcript becomes an ordinary message only when you send it.

## Model and language

`GEMINI_TRANSCRIPTION_MODEL` defaults to `gemini-3.5-transcribe-live`.
`GEMINI_TRANSCRIPTION_LANGUAGE` defaults to `en-US`, for English dictation. Set it to `fi-FI` for Finnish, another BCP-47 language code, or
`auto` for automatic detection. The token endpoint also accepts an optional
`language` of `en`, `fi`, or `auto`. `GET /api/voice` reports availability.
See Google's [ephemeral-token documentation](https://ai.google.dev/gemini-api/docs/ephemeral-tokens).

## Requirements

Microphone access requires HTTPS or localhost and browser permission. The browser
must be able to reach Gemini directly. Restart the backend and reload the frontend
after upgrading from the previous WebSocket relay implementation.

See [configuration](configuration.md) and the [voice API](api.md#voice) for all settings and endpoints.
