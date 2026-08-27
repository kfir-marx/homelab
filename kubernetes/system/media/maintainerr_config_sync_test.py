import importlib.util
import pathlib
import sys
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("maintainerr_config_sync.py")
SPEC = importlib.util.spec_from_file_location("maintainerr_config_sync", MODULE_PATH)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)


class FakeApi:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def request(self, path, **kwargs):
        method = kwargs.get("method", "GET")
        self.calls.append((method, path, kwargs.get("body")))
        return self.responses[(method, path)].pop(0)


class ConfigParsingTest(unittest.TestCase):
    def test_reads_arr_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.xml"
            path.write_text("<Config><ApiKey> secret </ApiKey></Config>")
            self.assertEqual(sync.read_arr_api_key(path, "Radarr"), "secret")

    def test_rejects_missing_seerr_secret(self):
        with self.assertRaisesRegex(RuntimeError, "jellyfin.apiKey"):
            sync.nested_secret({"jellyfin": {}}, "jellyfin", "apiKey")


class ReconcileTest(unittest.TestCase):
    def test_complete_matching_configuration_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            state_root = pathlib.Path(directory)
            for application, api_key in (
                ("radarr", "radarr-key"),
                ("sonarr", "sonarr-key"),
            ):
                application_path = state_root / application
                application_path.mkdir()
                (application_path / "config.xml").write_text(
                    f"<Config><ApiKey>{api_key}</ApiKey></Config>"
                )
            seerr_path = state_root / "seerr"
            seerr_path.mkdir()
            (seerr_path / "settings.json").write_text(
                '{"main":{"apiKey":"seerr-key"},'
                '"jellyfin":{"apiKey":"jellyfin-key"}}'
            )

            api = FakeApi(
                {
                    ("GET", "/api/settings/radarr"): [
                        [
                            {
                                "id": 1,
                                "serverName": "Radarr",
                                "url": "http://radarr:7878",
                                "apiKey": "radarr-key",
                            }
                        ]
                    ],
                    ("GET", "/api/settings/sonarr"): [
                        [
                            {
                                "id": 2,
                                "serverName": "Sonarr",
                                "url": "http://sonarr:8989",
                                "apiKey": "sonarr-key",
                            }
                        ]
                    ],
                    ("GET", "/api/settings/jellyfin"): [
                        {
                            "jellyfin_url": "http://jellyfin:8096",
                            "jellyfin_api_key": "jellyfin-key",
                        }
                    ],
                    ("GET", "/api/settings/seerr"): [
                        {
                            "url": "http://seerr:5055",
                            "api_key": "seerr-key",
                        }
                    ],
                }
            )

            self.assertEqual(sync.reconcile_integrations(api, state_root), [])
            self.assertEqual(len(api.calls), 4)

    def test_adds_missing_arr_server_after_successful_test(self):
        desired = {
            "serverName": "Radarr",
            "url": "http://radarr:7878",
            "apiKey": "secret",
        }
        api = FakeApi(
            {
                ("GET", "/api/settings/radarr"): [[]],
                ("POST", "/api/settings/test/radarr"): [
                    {"status": "OK", "code": 1}
                ],
                ("POST", "/api/settings/radarr"): [
                    {"status": "OK", "code": 1}
                ],
            }
        )

        self.assertTrue(sync.reconcile_arr(api, "Radarr", desired))
        self.assertEqual(api.calls[-1], ("POST", "/api/settings/radarr", desired))

    def test_matching_arr_server_is_unchanged(self):
        desired = {
            "serverName": "Sonarr",
            "url": "http://sonarr:8989",
            "apiKey": "secret",
        }
        api = FakeApi(
            {
                ("GET", "/api/settings/sonarr"): [[{"id": 2, **desired}]],
            }
        )

        self.assertFalse(sync.reconcile_arr(api, "Sonarr", desired))
        self.assertEqual(len(api.calls), 1)

    def test_rejects_ambiguous_arr_servers(self):
        api = FakeApi(
            {
                ("GET", "/api/settings/radarr"): [[{"id": 1}, {"id": 2}]],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "expected at most one"):
            sync.reconcile_arr(api, "Radarr", {})


if __name__ == "__main__":
    unittest.main()
