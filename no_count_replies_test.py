import sqlite3
import tempfile
import unittest
from pathlib import Path
from no_count_replies import create_incident, close_incident, summary, parse_reply, record_reply

class NoCountReplyTests(unittest.TestCase):
    def test_lifecycle_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'test.db'
            create_incident(db,'NC-20261019-104200','2026-10-19 10:42:00','2026-10-19 10:52:00')
            parsed=parse_reply('Re: TYRETRACK NO COUNT NC-20261019-104200','Reason: Waiting for tyres\nSupervisor: John\nComments: Truck late\n\nOn Monday wrote:\n> Reason: Fake')
            self.assertEqual(parsed[1],'Waiting for tyres')
            self.assertTrue(record_reply(db,*parsed,'<message-1>'))
            self.assertFalse(record_reply(db,*parsed,'<message-1>'))
            close_incident(db,'2026-10-19 11:00:00')
            self.assertIn('1 completed',summary(db,'2026-10-19'))
    def test_incomplete_rejected(self):
        self.assertIsNone(parse_reply('NC-20261019-104200','Reason: Other'))
        self.assertIsNone(parse_reply('hello','Reason: Other\nSupervisor: Jane'))

if __name__=='__main__':unittest.main()
