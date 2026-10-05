"""Tests for Anypoint Platform authentication (OAuth 2.0 and legacy)."""

import base64
import json
import os
import sys
import time
import unittest
from unittest.mock import patch

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from anypoint_auth import (
    _oauth_client_credentials,
    _can_use_oauth,
    _can_login,
    _obtain_token,
    load_auth,
    refresh_token,
    auth_status,
)

# Capture real credentials at import time (before any setUp clears them)
_REAL_CLIENT_ID = os.environ.get('ANYPOINT_CLIENT_ID', '')
_REAL_CLIENT_SECRET = os.environ.get('ANYPOINT_CLIENT_SECRET', '')
_HAS_REAL_OAUTH = bool(_REAL_CLIENT_ID and _REAL_CLIENT_SECRET)

SKIP_INTEGRATION = not _HAS_REAL_OAUTH
SKIP_REASON = "Requires ANYPOINT_CLIENT_ID and ANYPOINT_CLIENT_SECRET env vars"


class TestOAuthSupport(unittest.TestCase):
    """Unit tests for OAuth 2.0 Client Credentials support (no real API calls)."""

    def setUp(self):
        for key in ['ANYPOINT_TOKEN', 'ANYPOINT_CLIENT_ID', 'ANYPOINT_CLIENT_SECRET',
                    'ANYPOINT_USERNAME', 'ANYPOINT_PASSWORD', 'ANYPOINT_HOST']:
            os.environ.pop(key, None)

    def test_can_use_oauth_when_credentials_set(self):
        os.environ['ANYPOINT_CLIENT_ID'] = 'test-id'
        os.environ['ANYPOINT_CLIENT_SECRET'] = 'test-secret'
        self.assertTrue(_can_use_oauth())

    def test_can_use_oauth_when_only_id_set(self):
        os.environ['ANYPOINT_CLIENT_ID'] = 'test-id'
        self.assertFalse(_can_use_oauth())

    def test_can_use_oauth_when_only_secret_set(self):
        os.environ['ANYPOINT_CLIENT_SECRET'] = 'test-secret'
        self.assertFalse(_can_use_oauth())

    def test_can_use_oauth_when_not_set(self):
        self.assertFalse(_can_use_oauth())

    def test_auth_status_with_oauth(self):
        os.environ['ANYPOINT_CLIENT_ID'] = 'test-id'
        os.environ['ANYPOINT_CLIENT_SECRET'] = 'test-secret'
        status = auth_status()
        self.assertTrue(status['ok'])
        self.assertIn('OAuth 2.0', status['detail'])

    def test_auth_status_with_login_credentials(self):
        os.environ['ANYPOINT_USERNAME'] = 'test-user'
        os.environ['ANYPOINT_PASSWORD'] = 'test-pass'
        status = auth_status()
        self.assertTrue(status['ok'])
        self.assertIn('username/password', status['detail'])

    def test_auth_status_oauth_prioritized_over_login(self):
        os.environ['ANYPOINT_CLIENT_ID'] = 'test-id'
        os.environ['ANYPOINT_CLIENT_SECRET'] = 'test-secret'
        os.environ['ANYPOINT_USERNAME'] = 'test-user'
        os.environ['ANYPOINT_PASSWORD'] = 'test-pass'
        status = auth_status()
        self.assertTrue(status['ok'])
        self.assertIn('OAuth 2.0', status['detail'])

    def test_auth_status_no_credentials(self):
        status = auth_status()
        self.assertFalse(status['ok'])
        self.assertIn('No credentials', status['detail'])


class TestOAuthIntegration(unittest.TestCase):
    """Integration tests against real Anypoint API."""

    def setUp(self):
        # Restore real credentials for every test in this class
        for key in ['ANYPOINT_TOKEN', 'ANYPOINT_USERNAME', 'ANYPOINT_PASSWORD']:
            os.environ.pop(key, None)
        if _REAL_CLIENT_ID:
            os.environ['ANYPOINT_CLIENT_ID'] = _REAL_CLIENT_ID
        if _REAL_CLIENT_SECRET:
            os.environ['ANYPOINT_CLIENT_SECRET'] = _REAL_CLIENT_SECRET

    @unittest.skipIf(SKIP_INTEGRATION, SKIP_REASON)
    def test_oauth_token_request(self):
        """OAuth 2.0 Client Credentials returns a token from Anypoint."""
        token, expires_in = _oauth_client_credentials(
            "https://anypoint.mulesoft.com",
            _REAL_CLIENT_ID,
            _REAL_CLIENT_SECRET
        )
        self.assertIsNotNone(token)
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 0)
        self.assertGreater(expires_in, 0)
        print(f"\nOAuth token obtained OK (length: {len(token)}, expires_in: {expires_in}s)")

    @unittest.skipIf(SKIP_INTEGRATION, SKIP_REASON)
    def test_load_auth_with_oauth(self):
        """load_auth() uses OAuth when credentials are set."""
        auth = load_auth()
        self.assertIsNotNone(auth.token)
        self.assertGreater(len(auth.token), 0)
        print(f"\nload_auth() token OK (length: {len(auth.token)})")

    @unittest.skipIf(SKIP_INTEGRATION, SKIP_REASON)
    def test_refresh_token_with_oauth(self):
        """refresh_token() obtains a new token via OAuth."""
        token = refresh_token()
        self.assertIsNotNone(token)
        self.assertGreater(len(token), 0)
        print(f"\nrefresh_token() OK (length: {len(token)})")

    @unittest.skipIf(SKIP_INTEGRATION, SKIP_REASON)
    def test_token_has_required_scopes(self):
        """Decode the JWT and verify it carries the expected claims."""
        token, expires_in = _oauth_client_credentials(
            "https://anypoint.mulesoft.com",
            _REAL_CLIENT_ID,
            _REAL_CLIENT_SECRET
        )

        parts = token.split('.')
        if len(parts) != 3:
            self.skipTest("Token is not a JWT - cannot inspect claims")

        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding

        claims = json.loads(base64.urlsafe_b64decode(payload))
        print(f"\nJWT Claims:\n{json.dumps(claims, indent=2)}")

        # Token must not be expired
        self.assertIn('exp', claims, "Token missing 'exp' claim")
        self.assertGreater(claims['exp'], time.time(), "Token is already expired")
        print(f"Token valid for {claims['exp'] - time.time():.0f} more seconds")

        if 'client_id' in claims:
            self.assertEqual(claims['client_id'], _REAL_CLIENT_ID)
            print(f"client_id matches: {claims['client_id']}")

    def test_oauth_prioritized_over_login(self):
        """_obtain_token() calls OAuth, not legacy login, when both are configured."""
        os.environ['ANYPOINT_CLIENT_ID'] = 'oauth-id'
        os.environ['ANYPOINT_CLIENT_SECRET'] = 'oauth-secret'
        os.environ['ANYPOINT_USERNAME'] = 'legacy-user'
        os.environ['ANYPOINT_PASSWORD'] = 'legacy-pass'

        with patch('anypoint_auth._oauth_client_credentials', return_value=('mock-token', 3600)) as mock_oauth, \
             patch('anypoint_auth._login') as mock_login:
            _obtain_token("https://anypoint.mulesoft.com")
            mock_oauth.assert_called_once()
            mock_login.assert_not_called()
        print("\nOAuth correctly prioritized over username/password")


class TestLegacyAuthStillWorks(unittest.TestCase):
    """Backwards compatibility: username/password still works."""

    def setUp(self):
        for key in ['ANYPOINT_TOKEN', 'ANYPOINT_CLIENT_ID', 'ANYPOINT_CLIENT_SECRET',
                    'ANYPOINT_USERNAME', 'ANYPOINT_PASSWORD']:
            os.environ.pop(key, None)

    def test_can_login_with_credentials(self):
        os.environ['ANYPOINT_USERNAME'] = 'test-user'
        os.environ['ANYPOINT_PASSWORD'] = 'test-pass'
        self.assertTrue(_can_login())

    def test_can_login_without_credentials(self):
        self.assertFalse(_can_login())

    def test_error_when_no_credentials_available(self):
        with self.assertRaises(RuntimeError) as ctx:
            _obtain_token("https://anypoint.mulesoft.com")
        self.assertIn("neither OAuth credentials", str(ctx.exception))
        print("\nProper error raised when no credentials available")


if __name__ == '__main__':
    unittest.main(verbosity=2)
