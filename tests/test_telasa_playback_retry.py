"""Offline coverage for TELASA's playback-token retry loop."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from requests import ConnectionError, Response, Timeout
from requests.cookies import RequestsCookieJar

from unshackle.core.manifests import DASH, HLS
from unshackle.core.tracks import Tracks
from unshackle.services.TELASA import TELASA


class TestTelasaPlaybackTokenRetry(unittest.TestCase):
    def setUp(self) -> None:
        self.service = object.__new__(TELASA)
        self.service.config = {"endpoints": {
            "auth": "https://example.test/auth",
            "episode": "https://example.test/episodes",
            "graphql": "https://example.test/graphql",
        }}
        self.service.session = Mock()
        self.service.log = Mock()
        self.title = SimpleNamespace(id="episode-123")
        self.sleep_patch = patch("unshackle.services.TELASA.time.sleep")
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)


    def test_fractional_season_stays_distinct(self) -> None:
        self.assertEqual(self.service._normalise_season(0.5), 0.5)
        self.assertEqual(self.service._normalise_season("2.5"), 2.5)
        self.assertEqual(self.service._normalise_season(1), 1)

    def test_fractional_season_reaches_episode(self) -> None:
        self.service.session.post.return_value.json.return_value = {
            "data": {"items": [{"data": {
                "id": "episode-123", "name": "第1話", "subtitle": "Special",
            }}]}
        }
        episodes = self.service._process_episodes(["episode-123"], "Example Show", 0.5)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].season, 0.5)
        self.assertEqual(str(episodes[0]), "Example Show S0.5E01 Special")

    @patch("unshackle.services.TELASA.DASH.from_url")
    @patch("unshackle.services.TELASA.HLS.from_url")
    def test_dash_failure_falls_back_to_hls(self, hls_from_url: Mock, dash_from_url: Mock) -> None:
        self.service._get_playback_token = Mock(return_value="token")
        self.service.session.post.return_value.json.return_value = {
            "data": {"manifests": [
                {"protocol": "hls", "items": [{"name": "hd", "url": "https://example.test/video_hd.m3u8"}]},
                {"protocol": "dash", "items": [{"name": "hd", "url": "https://example.test/video_hd.mpd"}]},
            ]}
        }
        dash_from_url.side_effect = ConnectionError("MPD unavailable")
        hls_from_url.return_value.to_tracks.return_value = Tracks()

        tracks = self.service.get_tracks(self.title)

        dash_from_url.assert_called_once_with("https://example.test/video_fhd.mpd", self.service.session)
        hls_from_url.assert_called_once_with("https://example.test/video_fhd.m3u8", self.service.session)
        hls_from_url.return_value.to_tracks.assert_called_once_with(language="ja")
        self.assertIsInstance(tracks, Tracks)

    @patch("unshackle.services.TELASA.DASH.from_url")
    @patch("unshackle.services.TELASA.HLS.from_url")
    def test_missing_dash_uses_hls(self, hls_from_url: Mock, dash_from_url: Mock) -> None:
        self.service._get_playback_token = Mock(return_value="token")
        self.service.session.post.return_value.json.return_value = {
            "data": {"manifests": [
                {"protocol": "hls", "items": [{"name": "hd", "url": "https://example.test/video_hd.m3u8"}]},
            ]}
        }
        hls_from_url.return_value.to_tracks.return_value = Tracks()

        self.service.get_tracks(self.title)

        dash_from_url.assert_not_called()
        hls_from_url.assert_called_once_with("https://example.test/video_fhd.m3u8", self.service.session)

    @patch("unshackle.services.TELASA.DASH.from_url")
    def test_fhd_403_falls_back_to_hd(self, dash_from_url: Mock) -> None:
        response = Response()
        response.status_code = 403
        self.service.log = Mock()
        dash_from_url.side_effect = [
            ConnectionError("FHD unavailable", response=response),
            Mock(**{"to_tracks.return_value": Tracks()}),
        ]

        tracks = self.service._load_manifest_tracks(DASH, "https://example.test/video_hd.mpd")

        self.assertIsInstance(tracks, Tracks)
        self.assertEqual(
            [call.args[0] for call in dash_from_url.call_args_list],
            ["https://example.test/video_fhd.mpd", "https://example.test/video_hd.mpd"],
        )

    @patch("unshackle.services.TELASA.HLS.from_url")
    def test_hls_fhd_403_falls_back_to_hd(self, hls_from_url: Mock) -> None:
        response = Response()
        response.status_code = 403
        hls_from_url.side_effect = [
            ConnectionError("FHD unavailable", response=response),
            Mock(**{"to_tracks.return_value": Tracks()}),
        ]

        tracks = self.service._load_manifest_tracks(HLS, "https://example.test/video_hd.m3u8")

        self.assertIsInstance(tracks, Tracks)
        self.assertEqual(
            [call.args[0] for call in hls_from_url.call_args_list],
            ["https://example.test/video_fhd.m3u8", "https://example.test/video_hd.m3u8"],
        )

    def test_success_without_retry(self) -> None:
        self.service.session.post.return_value.json.return_value = {
            "data": {"playbackToken": {"token": "ok"}}
        }
        self.assertEqual(self.service._get_playback_token(self.title), "ok")
        self.assertEqual(self.service.session.post.call_count, 1)
        self.sleep.assert_not_called()

    def test_unknown_error_retries_then_succeeds(self) -> None:
        self.service.session.post.return_value.json.side_effect = [
            {"errors": [{"message": "unknown error"}]},
            {"errors": [{"message": "unknown error"}]},
            {"data": {"playbackToken": {"token": "ok"}}},
        ]
        self.assertEqual(self.service._get_playback_token(self.title), "ok")
        self.assertEqual(self.service.session.post.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.sleep.assert_called_with(5)

    def test_playback_error_refreshes_credentials_once(self) -> None:
        self.service._auth_method = "credentials"
        self.service.credential = object()
        self.service._authenticate_credentials = Mock()
        self.service.session.post.return_value.json.side_effect = [
            {"errors": [{"message": "unknown error"}]},
            {"data": {"playbackToken": {"token": "fresh"}}},
        ]
        self.assertEqual(self.service._get_playback_token(self.title), "fresh")
        self.service._authenticate_credentials.assert_called_once_with(
            self.service.credential, force_refresh=True
        )

    def test_credential_relogin_bypasses_stale_cache(self) -> None:
        self.service.credential = None
        self.service.session.headers = {"Authorization": "Bearer stale", "X-Device-Id": "old-device"}
        self.service.cache = Mock()
        self.service._resolve_cognito_username = Mock(return_value="mapped-user")
        self.service._check_subscription = Mock()
        credentials = SimpleNamespace(sha1="hash", username="user@example.test", password="secret")
        self.service.session.post.return_value.json.return_value = {
            "AuthenticationResult": {"AccessToken": "fresh"}
        }
        self.service._authenticate_credentials(credentials, force_refresh=True)
        self.assertEqual(self.service.cache.get.call_count, 1)
        self.assertEqual(self.service.session.headers["Authorization"], "Bearer fresh")
        self.assertNotEqual(self.service.session.headers["X-Device-Id"], "old-device")
        self.assertEqual(
            self.service.session.post.call_args.kwargs["json"]["AuthFlow"],
            "USER_PASSWORD_AUTH",
        )

    def test_playback_error_refreshes_cookies_once(self) -> None:
        self.service._auth_method = "cookies"
        self.service._authenticate_cookies = Mock()
        self.service.session.post.return_value.json.side_effect = [
            {"errors": [{"message": "unauthorized"}]},
            {"data": {"playbackToken": {"token": "fresh"}}},
        ]
        self.assertEqual(self.service._get_playback_token(self.title), "fresh")
        self.service._authenticate_cookies.assert_called_once_with(None, force_refresh=True)

    def test_cookie_refresh_replaces_old_access_token(self) -> None:
        prefix = f"CognitoIdentityServiceProvider.{TELASA.COGNITO_CLIENT_ID}"
        jar = RequestsCookieJar()
        jar.set(f"{prefix}.LastAuthUser", "test-user")
        jar.set(f"{prefix}.test-user.accessToken", "old-access")
        jar.set(f"{prefix}.test-user.refreshToken", "valid-refresh")
        jar.set("did", "device-id")
        self.service.session.cookies = jar
        self.service.session.headers = {}
        self.service.session.post.return_value.json.return_value = {
            "AuthenticationResult": {"AccessToken": "new-access", "RefreshToken": "new-refresh"}
        }
        self.service._check_subscription = Mock()
        self.service._authenticate_cookies(None, force_refresh=True)
        self.assertEqual(self.service.session.headers["Authorization"], "Bearer new-access")
        self.assertEqual(jar.get(f"{prefix}.test-user.accessToken"), "new-access")
        self.assertEqual(jar.get(f"{prefix}.test-user.refreshToken"), "new-refresh")
        self.assertEqual(self.service.session.post.call_count, 1)
        self.assertEqual(self.service.session.post.call_args.kwargs["json"]["AuthFlow"], "REFRESH_TOKEN_AUTH")

    def test_transport_error_is_retried(self) -> None:
        self.service.session.post.side_effect = [
            Timeout("connection timed out"),
            Mock(**{"json.return_value": {"data": {"playbackToken": {"token": "ok"}}}}),
        ]
        self.assertEqual(self.service._get_playback_token(self.title), "ok")
        self.assertEqual(self.sleep.call_count, 1)

    def test_missing_token_is_retried(self) -> None:
        self.service.session.post.return_value.json.side_effect = [
            {"data": {"playbackToken": None}},
            {"data": {"playbackToken": {"token": "ok"}}},
        ]
        self.assertEqual(self.service._get_playback_token(self.title), "ok")
        self.assertEqual(self.sleep.call_count, 1)

    def test_exhaustion_stops_after_twenty_retries(self) -> None:
        self.service.session.post.return_value.json.return_value = {
            "errors": [{"message": "unknown error"}]
        }
        with self.assertRaises(SystemExit) as exit_context:
            self.service._get_playback_token(self.title)
        self.assertEqual(exit_context.exception.code, 1)
        self.assertEqual(self.service.session.post.call_count, 21)
        self.assertEqual(self.sleep.call_count, 20)
        self.service.log.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
