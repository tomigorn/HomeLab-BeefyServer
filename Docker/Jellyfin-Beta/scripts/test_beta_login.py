import unittest

import beta_login


class Parse(unittest.TestCase):
    def test_code_is_six_digits(self):
        self.assertEqual(beta_login.parse(["123456", "jane"]), ("123456", "jane"))
        for bad in (["12345", "jane"], ["abcdef", "jane"], ["123456"], ["123456", "a b"]):
            with self.assertRaises(ValueError):
                beta_login.parse(bad)

    def test_user_found_case_insensitive(self):
        users = [{"Name": "Jane", "Id": "a-b"}, {"Name": "beta-admin", "Id": "c"}]
        self.assertEqual(beta_login.find(users, "jane"), "a-b")
        with self.assertRaises(KeyError):
            beta_login.find(users, "nobody")


if __name__ == "__main__":
    unittest.main()
