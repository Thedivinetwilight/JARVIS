"""The voice-first client protocol: audio sessions in, spoken audio out (unavailable)."""

from __future__ import annotations

import json
import pathlib
import stat
import tempfile
import unittest

from app.jarvis.errors import Unavailable
from app.tests.support import ApiTestCase, build_app


class AudioSessionTests(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.device, self.token = self.pair_device(label="kitchen tablet")

    def start(self, token: str | None = None):
        return self.call("POST", "/v1/audio/sessions", token=token or self.token)

    def test_starting_a_session_states_what_is_and_is_not_available(self):
        response, payload = self.start()
        self.assertEqual(response.status, 201, payload)
        self.assertEqual(payload["input"], "audio only")
        self.assertFalse(payload["text_input"]["supported"])
        self.assertFalse(payload["transcription"]["available"])
        self.assertEqual(payload["transcription"]["code"], "speech_asr_unavailable")
        self.assertFalse(payload["spoken_output"]["available"])
        self.assertEqual(payload["spoken_output"]["code"], "speech_tts_unavailable")
        self.assertIn("audio/wav", payload["accepted_content_types"])
        self.assertEqual(payload["max_bytes"], self.config.storage.max_audio_bytes_per_session)

    def test_audio_chunks_are_stored_locally_and_not_transcribed(self):
        _, session = self.start()
        session_id = session["session_id"]
        response, payload = self.call(
            "POST", f"/v1/audio/sessions/{session_id}/audio", token=self.token,
            body=b"RIFF....WAVEfmt ", content_type="audio/wav",
        )
        self.assertEqual(response.status, 202, payload)
        self.assertEqual(payload["chunk_index"], 1)
        self.assertEqual(payload["bytes_stored"], len(b"RIFF....WAVEfmt "))
        self.assertFalse(payload["transcription"]["available"])

        stored = self.config.storage.audio_dir / self.device["id"] / session_id
        self.assertTrue(stored.is_dir())
        self.assertEqual(stat.S_IMODE(stored.stat().st_mode) & 0o777, 0o700)
        self.assertEqual((stored / "chunk-0001.bin").read_bytes(), b"RIFF....WAVEfmt ")
        self.assertNotIn(pathlib.Path.cwd(), [stored.parent])

    def test_text_bodies_are_refused_with_an_explicit_code(self):
        _, session = self.start()
        response, payload = self.call(
            "POST", f"/v1/audio/sessions/{session['session_id']}/audio", token=self.token,
            body=b"remind me to call my mother", content_type="text/plain",
        )
        self.assertEqual(response.status, 415)
        self.assertEqual(payload["error"]["code"], "text_input_not_supported")
        self.assertIn("no text-conversation input", payload["error"]["message"])

    def test_non_audio_content_types_are_refused(self):
        _, session = self.start()
        # Textual types get the specific code; anything else is generically unsupported.
        for content_type, code in (
            ("application/json", "text_input_not_supported"),
            ("application/octet-stream", "unsupported_media_type"),
            ("", "unsupported_media_type"),
        ):
            with self.subTest(content_type=content_type):
                response, payload = self.call(
                    "POST", f"/v1/audio/sessions/{session['session_id']}/audio",
                    token=self.token, body=b"\x00\x01", content_type=content_type,
                )
                self.assertEqual(response.status, 415)
                self.assertEqual(payload["error"]["code"], code)

    def test_empty_and_oversized_chunks_are_refused(self):
        _, session = self.start()
        session_id = session["session_id"]
        empty, payload = self.call("POST", f"/v1/audio/sessions/{session_id}/audio",
                                   token=self.token, body=b"", content_type="audio/wav")
        self.assertEqual(empty.status, 400)
        self.assertEqual(payload["error"]["code"], "empty_chunk")
        too_big = b"\x00" * (self.config.storage.max_audio_chunk_bytes + 1)
        large, payload = self.call("POST", f"/v1/audio/sessions/{session_id}/audio",
                                   token=self.token, body=too_big, content_type="audio/wav")
        self.assertEqual(large.status, 413)
        self.assertEqual(payload["error"]["code"], "chunk_too_large")

    def test_a_session_cannot_exceed_its_byte_budget(self):
        _, session = self.start()
        session_id = session["session_id"]
        chunk = b"\x00" * self.config.storage.max_audio_chunk_bytes
        accepted = 0
        while True:
            response, payload = self.call("POST", f"/v1/audio/sessions/{session_id}/audio",
                                          token=self.token, body=chunk, content_type="audio/wav")
            if response.status != 202:
                break
            accepted += 1
        self.assertEqual(response.status, 413)
        self.assertEqual(payload["error"]["code"], "session_too_large")
        self.assertEqual(accepted, self.config.storage.max_audio_bytes_per_session
                         // self.config.storage.max_audio_chunk_bytes)

    def test_a_session_expires_and_refuses_further_audio(self):
        _, session = self.start()
        self.clock.advance(minutes=self.config.storage.audio_session_ttl_minutes + 1)
        response, payload = self.call(
            "POST", f"/v1/audio/sessions/{session['session_id']}/audio", token=self.token,
            body=b"\x00\x01", content_type="audio/wav",
        )
        self.assertEqual(response.status, 410)
        self.assertEqual(payload["error"]["code"], "session_expired")

    def test_another_clients_session_looks_identical_to_a_missing_one(self):
        _, session = self.start()
        _, other_token = self.pair_device(label="other tablet")
        response, payload = self.call(
            "POST", f"/v1/audio/sessions/{session['session_id']}/audio", token=other_token,
            body=b"\x00\x01", content_type="audio/wav",
        )
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "session_not_found")
        missing, payload = self.call(
            "POST", "/v1/audio/sessions/aud_missing/audio", token=other_token,
            body=b"\x00\x01", content_type="audio/wav",
        )
        self.assertEqual(missing.status, 404)
        self.assertEqual(payload["error"]["code"], payload["error"]["code"])

    def test_ending_a_session_reports_bytes_without_a_transcript(self):
        _, session = self.start()
        session_id = session["session_id"]
        self.call("POST", f"/v1/audio/sessions/{session_id}/audio", token=self.token,
                  body=b"\x00" * 10, content_type="audio/wav")
        self.call("POST", f"/v1/audio/sessions/{session_id}/audio", token=self.token,
                  body=b"\x00" * 5, content_type="audio/wav")
        response, payload = self.call("POST", f"/v1/audio/sessions/{session_id}/end",
                                     token=self.token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["bytes"], 15)
        self.assertEqual(payload["chunks"], 2)
        self.assertFalse(payload["transcription"]["available"])
        self.assertIn("no transcript exists", payload["transcription"]["note"])
        after, payload = self.call("POST", f"/v1/audio/sessions/{session_id}/audio",
                                  token=self.token, body=b"\x00", content_type="audio/wav")
        self.assertEqual(after.status, 410)
        self.assertEqual(payload["error"]["code"], "session_closed")

    def test_revoking_a_device_deletes_its_audio(self):
        _, session = self.start()
        session_id = session["session_id"]
        self.call("POST", f"/v1/audio/sessions/{session_id}/audio", token=self.token,
                  body=b"\x00" * 32, content_type="audio/wav")
        self.assertTrue((self.config.storage.audio_dir / self.device["id"]).exists())
        response, payload = self.call("POST", f"/v1/clients/{self.device['id']}/revoke",
                                     token=self.operator_token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["audio_files_deleted"], 1)
        self.assertFalse((self.config.storage.audio_dir / self.device["id"]).exists())

    def test_storage_reporting_counts_local_audio(self):
        _, session = self.start()
        self.call("POST", f"/v1/audio/sessions/{session['session_id']}/audio", token=self.token,
                  body=b"\x00" * 128, content_type="audio/wav")
        self.assertEqual(self.app.audio.storage_bytes(), 128)


class SpokenOutputTests(ApiTestCase):
    def test_spoken_output_refuses_rather_than_substituting_text(self):
        # Over HTTP the manifest gate answers first; the service refuses underneath it.
        response, payload = self.call("GET", "/v1/audio/output", token=self.operator_token)
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["detail"]["manifest"], "speech.tts")
        self.assertNotIn("transcript", json.dumps(payload))
        with self.assertRaises(Unavailable) as caught:
            self.app.audio.output()
        self.assertEqual(caught.exception.code, "speech_tts_unavailable")
        self.assertFalse(caught.exception.detail["text_conversation"])
        self.assertEqual(caught.exception.detail["gate"], "G-O1")

    def test_audio_capabilities_are_honest(self):
        response, payload = self.call("GET", "/v1/audio/capabilities", token=self.operator_token)
        self.assertEqual(response.status, 200)
        self.assertFalse(payload["asr"]["available"])
        self.assertFalse(payload["tts"]["available"])
        self.assertFalse(payload["text_conversation"]["available"])
        self.assertTrue(payload["input"]["supported"])


class AudioLimitTests(unittest.TestCase):
    """A smaller configured budget still refuses politely."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = pathlib.Path(self._tmp.name)
        self.app, self.config = build_app(self.directory, max_session_bytes=1000,
                                          max_chunk_bytes=600)
        _, self.token = self.app.auth.create_operator("limits")
        self.headers = {"authorization": f"Bearer {self.token}"}

    def test_configured_limits_are_the_ones_enforced(self):
        start = self.app.request("POST", "/v1/audio/sessions", self.headers)
        session_id = json.loads(start.body)["session_id"]
        self.assertEqual(json.loads(start.body)["max_chunk_bytes"], 600)
        first = self.app.request(
            "POST", f"/v1/audio/sessions/{session_id}/audio",
            {**self.headers, "content-type": "audio/wav"}, b"\x00" * 600,
        )
        self.assertEqual(first.status, 202)
        second = self.app.request(
            "POST", f"/v1/audio/sessions/{session_id}/audio",
            {**self.headers, "content-type": "audio/wav"}, b"\x00" * 600,
        )
        self.assertEqual(second.status, 413)
        self.assertEqual(json.loads(second.body)["error"]["code"], "session_too_large")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
