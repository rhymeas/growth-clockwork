from http.client import HTTPConnection
import json
from pathlib import Path
import threading
import unittest

from pipeline.review_api import create_server
from pipeline.task_broker import BrokerError, read_status
from pipeline.tests import test_task_broker, test_review_api


class BrokerStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_task_broker.BrokerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.database = self.fixture.root / 'broker.sqlite'

    def test_project_projection_does_not_expose_input_or_lease(self):
        task = self.fixture.admit()
        self.fixture.broker.claim('example', task['task_id'], agent='writer')
        self.fixture.admit(project='other')
        status = read_status(self.database, 'example')
        self.assertEqual(status['counts'], {'running': 1})
        self.assertEqual(len(status['tasks']), 1)
        for field in ('lease_token', 'input_ref', 'request_sha256', 'origin_key', 'permission_snapshot_id'):
            self.assertNotIn(field, status['tasks'][0])

    def test_bounded_projection_reports_truncation(self):
        for index in range(51):
            self.fixture.admit(origin_key=str(index))
        status = read_status(self.database, 'example')
        self.assertEqual(len(status['tasks']), 50)
        self.assertEqual(status['counts'], {'queued': 51})
        self.assertTrue(status['truncated'])

    def test_missing_database_is_not_created(self):
        path = self.fixture.root / 'absent.sqlite'
        with self.assertRaises(BrokerError):
            read_status(path, 'example')
        self.assertFalse(path.exists())

    def test_http_profile_boundary_origin_and_unavailable(self):
        desk = test_review_api.ReviewAPITests()
        desk.setUp()
        self.addCleanup(desk.tearDown)
        self.fixture.admit(project='alpha')
        server = create_server(desk.workspace, port=0, broker_database=self.database)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            for query, headers, expected in (
                ('project_id=alpha', {}, 200), ('project_id=unknown', {}, 404),
                ('project_id=alpha&project_id=beta', {}, 400),
                ('project_id=alpha', {'Origin': 'https://example.com'}, 403)):
                connection.request('GET', '/api/broker-status?' + query, headers=headers)
                response = connection.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, expected, body)
            server.review_service.broker_database = Path('/nonexistent-broker.sqlite')
            connection.request('GET', '/api/broker-status?project_id=alpha')
            response = connection.getresponse()
            self.assertEqual(response.status, 503, response.read())
            server.review_service.broker_database = None
            connection.request('GET', '/api/broker-status?project_id=alpha')
            response = connection.getresponse()
            self.assertEqual(json.loads(response.read())['state'], 'not_configured')
        finally:
            connection.close()
            server.shutdown()
            thread.join()
            server.server_close()
