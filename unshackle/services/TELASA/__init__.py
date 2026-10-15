from __future__ import annotations

import json
import math
import re
import sys
import time
import uuid
from hashlib import md5
from typing import Any, Generator, Optional, Union, List

import click
from click import Context
from langcodes import Language
from lxml.etree import XMLSyntaxError
from requests import Request, RequestException

from unshackle.core.constants import AnyTrack
from unshackle.core.search_result import SearchResult
from unshackle.core.service import Service
from unshackle.core.manifests import HLS, DASH
from unshackle.core.titles import Title_T, Titles_T, Episode, Movie, Movies, Series
from unshackle.core.tracks import Chapters, Tracks, Audio, Video, Subtitle
from unshackle.core.utils.collections import as_list

class TELASA(Service):
    """
    Service code for Telasa (https://www.telasa.jp/).
    \b
    Authorization: Cookies
    Security:
        Widevine:
            L3: 1080p (Possibly 2160p?)
        playReady:
            SL2000: 1080p
    Author: Crash@NSBC (Ported to Unshackle)
    """

    ALIASES = ("TLSA", "telasa")
    TITLE_RE = r"^(?:https?://(?:www\.)?telasa\.jp)/(?P<type>series|videos)/(?P<id>\d+)"
    GEOFENCE = ("JP",)
    PLAYBACK_TOKEN_MAX_RETRIES = 20
    PLAYBACK_TOKEN_RETRY_DELAY = 5

    @staticmethod
    @click.command(name="Telasa", short_help="https://www.telasa.jp/")
    @click.argument("title", type=str)
    @click.option("-s", "--single", is_flag=True, default=False, help="Download only the specific ID provided (do not fetch other seasons).")
    @click.option("-t", "--title", "force_title", type=str, default=None, help="Force a custom series/movie title.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Telasa:
        return TELASA(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, single: bool, force_title: str | None = None):
        self.title_input = title
        super().__init__(ctx)
        
        self.single = single
        self.force_title = force_title

        parse = re.search(self.TITLE_RE, self.title_input)
        if not parse:
            self.title_id = self.title_input
            self.is_movie = True 
        else:
            self.title_id = parse.group("id")
            self.is_movie = bool(parse.group("type") == "videos")

        self.cdm = ctx.obj.cdm
        self.session.headers.update({
            "Origin": "https://www.telasa.jp",
            "Referer": "https://www.telasa.jp/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })


    COGNITO_CLIENT_ID  = "71t3jfmis742ffmevo4677evms"
    EMAIL_MAPPING_URL  = "https://api-videopass-login.kddi-video.com/v1/account/openid/email-mapping"
    COGNITO_HEADERS = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-User-Agent": "aws-amplify/5.0.4 auth framework/2",
    }

    def authenticate(self, cookies: Optional[Any] = None, credential: Optional[Any] = None) -> None:
        if getattr(self.ctx.parent, "info_name", None) == "search":
            return
        if credential:
            self._authenticate_credentials(credential)
            self._auth_method = "credentials"
        elif cookies:
            self._authenticate_cookies(cookies)
            self._auth_method = "cookies"
        else:

            self.log.error("Service requires Cookies or Credentials for Authentication.", exc_info=False)
            sys.exit(1)

    def _resolve_cognito_username(self, email: str) -> str:
        """Map email address to Cognito UUID username via TELASA login API."""
        try:
            resp = self.session.get(
                self.EMAIL_MAPPING_URL,
                params={"email": email},
                headers={
                    "Origin": "https://www.telasa.jp",
                    "Referer": "https://www.telasa.jp/",
                    "X-Device-ID": str(uuid.uuid4()),
                },
            )
            data = resp.json()
            if isinstance(data.get("data"), str):
                username = data["data"]
            else:
                username = data.get("username") or data.get("user_name") or data.get("sub") or data.get("id")
            if username:
                self.log.info(f" + Resolved email to Cognito UUID: {username}")
                return username
        except Exception as e:
            self.log.warning(f" - Email mapping failed ({e}), falling back to email as username")
        return email

    def _authenticate_credentials(self, credential, force_refresh: bool = False) -> None:
        self.log.info(" + Logging into Telasa with credentials...")
        super().authenticate(None, credential)

        cache_key = f"tokens_{credential.sha1}"
        cache = self.cache.get(cache_key) if not force_refresh else None

        if cache:
            try:
                cached = cache.data
                self.session.headers.update({
                    "Authorization": f"Bearer {cached['access_token']}",
                    "X-Device-Id": cached["device_id"],
                })
                self.log.info(" + Using cached tokens...")
                
                # Verify token validity to prevent downstream NoneType errors
                verify_req = self.session.get("https://api-videopass.kddi-video.com/v2/users/me")
                if not verify_req.ok or not verify_req.json().get("data"):
                    raise ValueError("Token rejected by server")
                
                self._check_subscription()
                return
            except Exception as e:
                self.log.warning(f" - Cached token invalid ({e}), re-authenticating...")
                self.session.headers.pop("Authorization", None)
                self.session.headers.pop("X-Device-Id", None)

        # Never let the new login reuse a rejected bearer token.
        self.session.headers.pop("Authorization", None)
        self.session.headers.pop("X-Device-Id", None)
        cognito_username = self._resolve_cognito_username(credential.username)

        req = self.session.post(
            url=self.config["endpoints"]["auth"],
            headers={
                **self.COGNITO_HEADERS,
                "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            },
            json={
                "ClientId": self.COGNITO_CLIENT_ID,
                "AuthFlow": "USER_PASSWORD_AUTH",
                "AuthParameters": {
                    "USERNAME": cognito_username,
                    "PASSWORD": credential.password,
                },
            },
        )

        data = req.json()

        if data.get("__type"):
            self.log.error(f" - Credential login failed: {data.get('message', data['__type'])}", exc_info=False)
            sys.exit(1)

        access_token = data["AuthenticationResult"]["AccessToken"]
        device_id = str(uuid.uuid4())

        import jwt as pyjwt
        try:
            exp = pyjwt.decode(access_token, options={"verify_signature": False}).get("exp")
        except Exception:
            exp = None
            
        self.cache.get(cache_key).set(
            {"access_token": access_token, "device_id": device_id},
            expiration=exp,
        )

        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "X-Device-Id": device_id,
        })
        self.log.info(" + Authentication Successful.")
        self._check_subscription()

    def _authenticate_cookies(self, cookies, force_refresh: bool = False) -> None:
        super().authenticate(cookies, None)

        self.log.info(" + Logging into Telasa with cookies...")

        last_auth_user = next(
            (cookie.value for cookie in self.session.cookies if cookie.name.endswith("LastAuthUser")),
            None,
        )
        if not last_auth_user:
            self.log.error(" - LastAuthUser not Found in cookies.", exc_info=False)
            sys.exit(1)

        prefix = f"CognitoIdentityServiceProvider.{self.COGNITO_CLIENT_ID}"
        access_token_cookie = next(
            (cookie for cookie in self.session.cookies if cookie.name == f"{prefix}.{last_auth_user}.accessToken"),
            None,
        )

        if not access_token_cookie:
            self.log.error(" - Access Token not Found in cookies.", exc_info=False)
            sys.exit(1)

        access_token = access_token_cookie.value

        if not force_refresh:
            req = self.session.post(
                url=self.config["endpoints"]["auth"],
                headers={**self.COGNITO_HEADERS, "X-Amz-Target": "AWSCognitoIdentityProviderService.GetUser"},
                json={"AccessToken": access_token},
            )
            data = req.json()
            if data.get("__type") == "NotAuthorizedException":
                force_refresh = True
            elif data.get("__type"):
                self.log.error(f" - Telasa authentication failed -> {data.get('message', data['__type'])}", exc_info=False)
                sys.exit(1)

        if force_refresh:
            self.log.info(" - Refreshing Telasa access token...")
            refresh_token = next(
                (cookie for cookie in self.session.cookies if cookie.name == f"{prefix}.{last_auth_user}.refreshToken"),
                None,
            )
            if not refresh_token:
                self.log.error(" - Refresh Token not Found; re-export cookies from browser.", exc_info=False)
                sys.exit(1)

            req = self.session.post(
                url=self.config["endpoints"]["auth"],
                headers={**self.COGNITO_HEADERS, "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"},
                json={
                    "ClientId": self.COGNITO_CLIENT_ID,
                    "AuthFlow": "REFRESH_TOKEN_AUTH",
                    "AuthParameters": {"REFRESH_TOKEN": refresh_token.value},
                },
            )
            data = req.json()
            result = data.get("AuthenticationResult") or {}
            if data.get("__type") or not result.get("AccessToken"):
                self.log.error(
                    f" - Failed to Refresh Access Token -> {data.get('message') or data.get('__type') or 'no token returned; re-export cookies from browser'}",
                    exc_info=False,
                )
                sys.exit(1)
            access_token = result["AccessToken"]
            access_token_cookie.value = access_token
            if result.get("RefreshToken"):
                refresh_token.value = result["RefreshToken"]

        device_id = next(
            (cookie for cookie in self.session.cookies if cookie.name == "did"),
            None,
        )
        if not device_id:
            self.log.error(" - Device ID (did) not Found in cookies.", exc_info=False)
            sys.exit(1)

        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "X-Device-Id": device_id.value,
        })
        self.log.info(" + Authentication Successful.")
        self._check_subscription()

    def _check_subscription(self) -> None:
        try:
            req = self.session.get("https://api-videopass.kddi-video.com/v2/users/me")
            data = req.json()
            me = data.get("data")
            
            if not me:
                self.log.warning(" - Could not verify subscription: Profile data not found.")
                return

            active = next((s for s in (me.get("subscriptions") or []) if s.get("status") == "ongoing"), None)
            if active:
                try:
                    plan_name = self.session.get("https://api-payment.kddi-video.com/api/v1/gmo/subscription_information/1").json().get("data", {}).get("plan_name") or active.get("code_name") or "Subscribed"
                except Exception:
                    plan_name = active.get("code_name") or "Subscribed"
                self.log.info(f" + Subscription: {plan_name}")
            else:
                self.log.warning(" - No active subscription found on this account.")
        except Exception as e:
            self.log.warning(f" - Could not verify subscription: {e}")


    def search(self) -> Generator[SearchResult, None, None]:
        headers = self.session.headers.copy()
        headers.update({
            "Content-Type": "application/json",
            "X-Device-Id": self.session.headers.get("X-Device-Id") or str(uuid.uuid4()),
        })
        
        req = self.session.post(
            url="https://api-videopass-anon.kddi-video.com/v2/search/videos",
            headers=headers,
            params={"page": 1, "per_page": 20},
            json={"query": self.title_input, "video_type": "unlimited"},
        )
        req.raise_for_status()
        data = req.json()
        items = (data.get("data") or {}).get("result_items", [])
        
        for item in items:
            node = item.get("data") or {}
            if not node:
                continue
            series_id = node.get("id")
            if not series_id or not re.fullmatch(r"\d+", str(series_id)):
                continue
            title = node.get("name") or "Unknown"
            synopses = node.get("synopses", {})
            desc = ""
            if isinstance(synopses, dict):
                desc = synopses.get("long") or synopses.get("medium") or synopses.get("short") or ""
            

            yield SearchResult(
                id_=f"https://www.telasa.jp/series/{series_id}",
                title=title,
                description=desc[:200] if desc else "",
                label=None,
                url=f"https://www.telasa.jp/series/{series_id}",
            )

    def get_titles(self) -> Titles_T:
        if self.is_movie:
            return self._get_movie_titles()
        else:
            return self._get_series_titles()

    def _get_movie_titles(self) -> Movies:
        req = self.session.get(f"https://api-videopass.kddi-video.com/v3/videos/{self.title_id}")
        if not req.ok:
            self.log.error(f"Could not fetch video {self.title_id}: HTTP {req.status_code}", exc_info=False)
            sys.exit(1)
            
        data = req.json()
        self.log.debug(data)
        if not data.get("data"):
            self.log.error("Failed to parse video data from response.", exc_info=False)
            sys.exit(1)

        video_detail = data["data"]
        series_ids = video_detail.get("series_ids") or []

        if not series_ids:
            return Movies([
                Movie(
                    id_=video_detail["id"],
                    service=self.__class__,
                    name=self.force_title or video_detail["name"],
                    year=video_detail.get("year_of_production"),
                    data=video_detail
                )
            ])

        series_id = series_ids[0]
        req_series = self.session.get(self.config["endpoints"]["series"].format(id=series_id))
        if not req_series.ok or not req_series.json().get("data"):
            self.log.error(f"Could not fetch series {series_id} metadata.", exc_info=False)
            sys.exit(1)

        series_detail = req_series.json()["data"]

        all_episodes = self._process_episodes(
            series_detail.get("episode_ids", []),
            self.force_title or series_detail.get("name", "Unknown Title"),
            series_detail.get("season_sequence_number", 1)
        )

        if not self.single and "seasons" in series_detail:
            for season in series_detail["seasons"]:
                if str(season["series_id"]) == str(series_id):
                    continue

                req_season = self.session.get(self.config["endpoints"]["series"].format(id=season["series_id"]))
                if req_season.ok and req_season.json().get("data"):
                    s_data = req_season.json()["data"]
                    extra_eps = self._process_episodes(
                        s_data.get("episode_ids", []),
                        self.force_title or s_data.get("name", "Unknown Title"),
                        s_data.get("season_sequence_number")
                    )
                    all_episodes.extend(extra_eps)

        return Series(all_episodes)

    def _get_series_titles(self) -> Series:
        req = self.session.get(self.config["endpoints"]["series"].format(id=self.title_id))
        if not req.ok:
            self.log.error(f"Failed to fetch series metadata: HTTP {req.status_code}", exc_info=False)
            sys.exit(1)
            
        data_json = req.json()
        self.log.debug(data_json)
        if not data_json.get("data"):
            self.log.error("Failed to parse series data from response. Ensure you have access.", exc_info=False)
            sys.exit(1)
            
        data = data_json["data"]
        
        all_episodes = self._process_episodes(
            data.get("episode_ids", []), 
            self.force_title or data.get("name", "Unknown Title"), 
            data.get("season_sequence_number", 1)
        )

        if not self.single:
            if "seasons" in data:
                for season in data["seasons"]:
                    if str(season["series_id"]) == str(self.title_id):
                        continue
                    
                    req_season = self.session.get(self.config["endpoints"]["series"].format(id=season["series_id"]))
                    if req_season.ok and req_season.json().get("data"):
                        s_data = req_season.json()["data"]
                        extra_eps = self._process_episodes(
                            s_data.get("episode_ids", []), 
                            self.force_title or s_data.get("name", "Unknown Title"), 
                            s_data.get("season_sequence_number")
                        )
                        all_episodes.extend(extra_eps)
            
            spinoff_keys = ["spinoffs", "spin_offs", "spinoff", "related", "related_series", "related_videos"]
            for sp_key in spinoff_keys:
                if sp_key in data:
                    for spinoff in data[sp_key]:
                        spinoff_id = spinoff.get("series_id") or spinoff.get("id")
                        if not spinoff_id or str(spinoff_id) == str(self.title_id):
                            continue
                        
                        req_spinoff = self.session.get(self.config["endpoints"]["series"].format(id=spinoff_id))
                        if req_spinoff.ok and req_spinoff.json().get("data"):
                            sp_data = req_spinoff.json()["data"]
                            extra_eps = self._process_episodes(
                                sp_data.get("episode_ids", []), 
                                self.force_title or sp_data.get("name", "Spin-off"), 
                                sp_data.get("season_sequence_number", 0) 
                            )
                            all_episodes.extend(extra_eps)

        return Series(all_episodes)


    def _normalise_season(self, season_number) -> int | float:
        """Keep TELASA's fractional season numbers distinct from whole seasons."""
        if season_number is None or isinstance(season_number, bool):
            return 1
        try:
            value = float(season_number)
        except (TypeError, ValueError):
            self.log.warning(f"Unexpected season number {season_number!r}, using season 1")
            return 1
        if not math.isfinite(value):
            self.log.warning(f"Unexpected season number {season_number!r}, using season 1")
            return 1
        return int(value) if value.is_integer() else value

    def _process_episodes(self, video_ids: list, series_name: str, season_number: int | float) -> list[Episode]:
        if not video_ids:
            return []
            
        req = self.session.post(
            url=self.config["endpoints"]["episode"],
            json={"video_ids": video_ids}
        )
        episodes_json = req.json()
        
        results = []
        skip_keywords = ["PR", "予告", "ダイジェスト", "特報", "解説放送"]

        items = episodes_json.get("data", {}).get("items", [])
        for number, item in enumerate(items):
            episode_data = item.get("data", {})
            name = episode_data.get("name", "")
            subtitle = episode_data.get("subtitle", "")
            
            full_title_info = f"{name} {subtitle}"
            if any(keyword in full_title_info for keyword in skip_keywords):
                continue

            episode_number_match = re.search(r"第(\d+)話", name)
            ep_num = int(episode_number_match.group(1)) if episode_number_match else (number + 1)

            results.append(
                Episode(
                    id_=episode_data.get("id"),
                    service=self.__class__,
                    title=series_name,
                    season=self._normalise_season(season_number),
                    number=ep_num,
                    name=subtitle,
                    year=episode_data.get("year_of_production"),
                    data=episode_data
                )
            )
        return results

    def _refresh_playback_auth(self) -> bool:
        """Replace stale auth without reusing cached credentials or cookie access tokens."""
        method = getattr(self, "_auth_method", None)
        if method == "credentials" and self.credential:
            self.log.warning("Playback token rejected; logging into Telasa again...")
            self._authenticate_credentials(self.credential, force_refresh=True)
            return True
        if method == "cookies":
            self.log.warning("Playback token rejected; refreshing Telasa cookie login...")
            self._authenticate_cookies(None, force_refresh=True)
            return True
        return False

    def _get_playback_token(self, title: Title_T) -> str:
        """Refresh auth once on a playback error, then retry with a bounded delay."""
        refreshed_auth = False
        for attempt in range(self.PLAYBACK_TOKEN_MAX_RETRIES + 1):
            should_refresh_auth = False
            if attempt:
                self.log.warning(
                    f"Retrying playback token ({attempt}/{self.PLAYBACK_TOKEN_MAX_RETRIES}) "
                    f"in {self.PLAYBACK_TOKEN_RETRY_DELAY}s..."
                )
                time.sleep(self.PLAYBACK_TOKEN_RETRY_DELAY)

            try:
                req = self.session.post(
                    url=self.config["endpoints"]["graphql"],
                    json={
                        "query": f'{{ playbackToken( item_id: "{title.id}", item_type: Mezzanine ) {{ token expires_at license_id }} }}'
                    },
                )
                data = req.json()
            except (RequestException, ValueError) as exc:
                error = str(exc)
            else:
                if not isinstance(data, dict):
                    error = "Invalid playback token response"
                elif data.get("errors"):
                    should_refresh_auth = True
                    error = "; ".join(
                        str(item.get("message", item)) if isinstance(item, dict) else str(item)
                        for item in data["errors"]
                    )
                else:
                    payload = data.get("data")
                    token_data = payload.get("playbackToken") if isinstance(payload, dict) else None
                    playback_token = token_data.get("token") if isinstance(token_data, dict) else None
                    if playback_token:
                        if attempt:
                            self.log.info(f" + Playback token obtained after {attempt} retries.")
                        return playback_token
                    error = "Playback token missing from response"
                    should_refresh_auth = True

            if attempt == self.PLAYBACK_TOKEN_MAX_RETRIES:
                self.log.error(f"Failed to Get Playback Token after {attempt + 1} attempts -> {error}", exc_info=False)
                sys.exit(1)
            self.log.warning(f"Failed to Get Playback Token ({attempt + 1}/{self.PLAYBACK_TOKEN_MAX_RETRIES + 1}) -> {error}")
            # A GraphQL error or missing token can mean the login expired. Re-authenticate
            # once per title; transport/JSON failures alone do not invalidate a login.
            if should_refresh_auth and not refreshed_auth:
                refreshed_auth = self._refresh_playback_auth()

        raise AssertionError("Unreachable: playback token retry loop did not terminate")

    def get_tracks(self, title: Title_T) -> Tracks:
        playback_token = self._get_playback_token(title)

        req = self.session.post(
            url=self.config["endpoints"]["graphql"],
            headers={
                "X-Device-Id": self.session.headers.get("X-Device-Id", "979e52a4-d9aa-4dbb-b97b-f33434aa5aa7"),
            },
            json={
                "query": f'{{ manifests(item_id: "{title.id}", item_type: Mezzanine, playback_token: "{playback_token}") {{ protocol items {{ name url }} }} subtitles(id: "{title.id}", playback_token: "{playback_token}") {{ language url }} mezzanine(id: "{title.id}") {{ id title time {{ last_played duration endStart }} recommend {{ previous {{ id title images {{ url }} }} next {{ id title images {{ url }} }} }} video {{ id }} }} thumbnailSeekings(id: "{title.id}", playback_token: "{playback_token}") {{ quality url }} }}'
            },
        )
        data = req.json()
        
        manifests = data["data"].get("manifests", [])
        manifest = next((x for x in manifests if x.get("protocol") == "dash"), None)
        hls_manifest = next((x for x in manifests if x.get("protocol") == "hls"), None)
        self.log.debug(data)

        tracks = Tracks()

        if manifest:
            try:
                manifest_url = manifest["items"][0]["url"]
                tracks.add(DASH.from_url(manifest_url, self.session).to_tracks(language="ja"))
            except (RequestException, ValueError, KeyError, IndexError, XMLSyntaxError) as exc:
                self.log.warning(f"Failed to load Telasa DASH manifest; falling back to HLS: {exc}")
                manifest = None

        if not manifest:
            if not hls_manifest:
                raise ValueError("TELASA did not return a usable DASH or HLS manifest.")
            manifest_url = hls_manifest["items"][0]["url"]
            tracks.add(HLS.from_url(manifest_url, self.session).to_tracks(language="ja"))

        for track in tracks:
            track.extra = {"playback_token": playback_token}
            if isinstance(track, Audio):
                track.language = Language.get("ja")

        if data.get("data", {}).get("subtitles"):
            for sub in data["data"]["subtitles"]:
                subtitle_url = sub.get("url")
                lang_code = sub.get("language", "ja")
                
                if not subtitle_url:
                    continue

                try:
                    cek = self.session.get(url=subtitle_url)
                    content_text = cek.text.strip()
                    if not cek.ok or not content_text.startswith("WEBVTT"):
                        continue
                except Exception as e:
                    continue

                track_id = md5(subtitle_url.encode()).hexdigest()[0:6]
                
                tracks.add(
                    Subtitle(
                        id_=track_id,
                        url=subtitle_url,
                        codec=Subtitle.Codec.WebVTT,
                        language=Language.get(lang_code),
                        forced=False,
                        sdh=False,
                    )
                )

        return tracks

    def get_chapters(self, title: Title_T) -> Chapters:
        return []

    def get_widevine_service_certificate(self, **kwargs) -> Optional[bytes]:
        return None

    def get_widevine_license(self, challenge: bytes, track: AnyTrack, **kwargs) -> Optional[bytes]:
        try:
            token = track.extra.get("playback_token")
            if not token:
                self.log.error("License Error: 'playback_token' not found in track data.")
                return None

            req = self.session.post(
                url="https://license.kddi-video.com/",
                data=challenge,
                headers={
                    "X-Custom-Data": f"token_type=playback&token_value={token}&widevine_security_level=L3"
                },
            )
            
            if not req.ok:
                self.log.error(f"License Request Failed: HTTP {req.status_code}")
                self.log.debug(f"Server Response: {req.text}")
                return None
                
            return req.content
            
        except Exception as e:
            self.log.error(f"Exception during license request: {e}")
            return None

    def get_playready_license(self, challenge: bytes, track: AnyTrack, **kwargs) -> Optional[bytes]:
        try:
            token = track.extra.get("playback_token")
            if not token:
                return None

            req = self.session.post(
                url=self.config["endpoints"]["license"]["playready"],
                data=challenge,
                headers={
                    "X-Custom-Data": f"token_type=playback&token_value={token}&playready_security_level=SL2000"
                },
            )
            if not req.ok:
                return None
            return req.content
        except Exception:
            return None