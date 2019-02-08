# -*- coding: utf-8 -*-
PWFILE = "tests/testdata/passwords"

from .base import MyTestCase
from privacyidea.lib.error import ParameterError
from privacyidea.lib.resolver import (save_resolver)
from privacyidea.lib.realm import (set_realm)
from privacyidea.lib.user import (User)
from privacyidea.lib.tokenclass import DATE_FORMAT
from privacyidea.lib.utils import b32encode_and_unicode
from privacyidea.lib.tokens.pushtoken import PushTokenClass
from privacyidea.models import (Token,
                                 Config,
                                 Challenge)
from privacyidea.lib.config import (set_privacyidea_config, set_prepend_pin)
from privacyidea.lib.policy import (PolicyClass, SCOPE, set_policy,
                                    delete_policy)
import binascii
import datetime
import hashlib
import base64
from dateutil.tz import tzlocal
import json

from passlib.utils.pbkdf2 import pbkdf2


class PushTokenTestCase(MyTestCase):

    serial1 = "PUSH00001"

    def test_01_create_token(self):
        db_token = Token(self.serial1, tokentype="push")
        db_token.save()
        token = PushTokenClass(db_token)
        self.assertEqual(token.token.serial, self.serial1)
        self.assertEqual(token.token.tokentype, "push")
        self.assertEqual(token.type, "push")
        class_prefix = token.get_class_prefix()
        self.assertEqual(class_prefix, "PIPU")
        self.assertEqual(token.get_class_type(), "push")

        # Test to do the 2nd step, although the token is not yet in clientwait
        self.assertRaises(ParameterError, token.update, {"otpkey": "1234", "pubkey": "1234", "serial": self.serial1})

        # Run enrollment step 1
        token.update({"genkey": 1})

        # Now the token is in the state clientwait, but insufficient parameters would still fail
        self.assertRaises(ParameterError, token.update, {"otpkey": "1234"})
        self.assertRaises(ParameterError, token.update, {"otpkey": "1234", "pubkey": "1234"})

        detail = token.get_init_detail()
        self.assertEqual(detail.get("serial"), self.serial1)
        self.assertEqual(detail.get("rollout_state"), "clientwait")
        enrollment_credential = detail.get("enrollment_credential")
        self.assertTrue("pushurl" in detail)
        self.assertFalse("otpkey" in detail)

        # Run enrollment step 2
        token.update({"enrollment_credential": enrollment_credential,
                      "serial": self.serial1,
                      "fbtoken": "firebasetoken",
                      "pubkey": "pubkey"})
        self.assertEqual(token.get_tokeninfo("firebase_token"), "firebasetoken")
        self.assertEqual(token.get_tokeninfo("public_key_smartphone"), "pubkey")
        self.assertTrue(token.get_tokeninfo("public_key_server").startswith("-----BEGIN RSA PUBLIC KEY-----"),
                        token.get_tokeninfo("public_key_server"))

        detail = token.get_init_detail()
        self.assertEqual(detail.get("rollout_state"), "enrolled")
        self.assertTrue(detail.get("public_key").startswith("-----BEGIN RSA PUBLIC KEY-----"))

    def test_02_api_enroll(self):
        self.authenticate()

        # 1st step
        with self.app.test_request_context('/token/init',
                                           method='POST',
                                           data={"type": "push",
                                                 "genkey": 1},
                                           headers={'Authorization': self.at}):
            res = self.app.full_dispatch_request()
            self.assertTrue(res.status_code == 200, res)
            detail = json.loads(res.data.decode('utf8')).get("detail")
            serial = detail.get("serial")
            self.assertEqual(detail.get("rollout_state"), "clientwait")
            self.assertTrue("pushurl" in detail)
            self.assertFalse("otpkey" in detail)
            enrollment_credential = detail.get("enrollment_credential")

        # 2nd step. Failing with wrong serial number
        with self.app.test_request_context('/ttype/push',
                                           method='POST',
                                           data={"serial": "wrongserial",
                                                 "pubkey": "pubkey",
                                                 "fbtoken": "firebaseT"}):
            res = self.app.full_dispatch_request()
            self.assertTrue(res.status_code == 400, res)
            status = json.loads(res.data.decode('utf8')).get("result").get("status")
            self.assertFalse(status)
            error = json.loads(res.data.decode('utf8')).get("result").get("error")
            self.assertEqual(error.get("message"),
                             "ERR905: No token with this serial number in the rollout state 'clientwait'.")

        # 2nd step. Fails with missing enrollment credential
        with self.app.test_request_context('/ttype/push',
                                           method='POST',
                                           data={"serial": serial,
                                                 "pubkey": "pubkey",
                                                 "fbtoken": "firebaseT"}):
            res = self.app.full_dispatch_request()
            self.assertTrue(res.status_code == 400, res)
            status = json.loads(res.data.decode('utf8')).get("result").get("status")
            self.assertFalse(status)
            error = json.loads(res.data.decode('utf8')).get("result").get("error")
            self.assertEqual(error.get("message"),
                             "ERR905: Invalid enrollment credential. You are not authorized to finalize this token.")

        # 2nd step: as performed by the smartphone
        with self.app.test_request_context('/ttype/push',
                                           method='POST',
                                           data={"enrollment_credential": enrollment_credential,
                                                 "serial": serial,
                                                 "pubkey": "pubkey",
                                                 "fbtoken": "firebaseT"}):
            res = self.app.full_dispatch_request()
            self.assertTrue(res.status_code == 200, res)
            detail = json.loads(res.data.decode('utf8')).get("detail")
            # still the same serial number
            self.assertEqual(serial, detail.get("serial"))
            self.assertEqual(detail.get("rollout_state"), "enrolled")
            # Now the smartphone gets a public key from the server
            self.assertTrue(detail.get("public_key").startswith("-----BEGIN RSA PUBLIC KEY-----"))

