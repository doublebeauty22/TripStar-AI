"""Synthetic provenance regressions through the real Python -> JS signer."""
import io
import json
import os
import random
import shutil
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import execjs
import execjs._external_runtime as external_runtime

from backend.tests.network_guard import assert_network_guard_active
from backend.app.services import xhs_service
from backend.app.services.xhs_sign import sign_util


class XHSSigningInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_network_guard_active()
        node = shutil.which('node')
        if not node:
            raise unittest.SkipTest('real signing tests require Node with network-denying permissions')
        folder = Path(__file__).resolve().parent
        sign_folder = Path(sign_util.__file__).resolve().parent
        cls.command = [node, '--permission', '--allow-fs-read=' + str(folder),
                       '--allow-fs-read=' + str(sign_folder),
                       '--allow-fs-read=' + str(sign_folder.parents[2] / 'node_modules'),
                       '--require', str(folder / 'xhs_sign_probe.cjs')]
        check = subprocess.run(cls.command + ['-e', 'process.stdout.write("ok")'],
                               capture_output=True, text=True, timeout=10,
                               env={'PATH': os.environ.get('PATH', '')})
        if check.returncode or check.stdout != 'ok':
            raise unittest.SkipTest('Node must deny network, child, worker and FFI permissions')

    def setUp(self):
        self.expected = threading.local()
        self.events = []
        self.lock_observations = []
        original_source = sign_util._xs_js._source
        # Observe the environment boundary and function inputs, without replacing
        # mnsv2, seccore_signv2, XsCommon, or their returned values.
        observed = original_source.replace(
            'let val = Reflect.get(target, prop, receiver);',
            'let val = Reflect.get(target, prop, receiver); __xhsProbe.read(prop, val);',
        ).replace(
            'function seccore_signv2(url_param, json_data) {',
            'function seccore_signv2(url_param, json_data) { __xhsProbe.core();',
        ).replace(
            'function XsCommon(a1, xs, xt) {',
            'function XsCommon(a1, xs, xt) { __xhsProbe.common(a1);',
        )
        self.assertTrue(observed != original_source, 'probe must attach to the real signer')
        self.instrumented_source = observed
        observed = ('function __observed_sign(args, expected, failCommon) {\n'
                    '__xhsProbe.begin(expected, failCommon);\ntry {\n' + observed +
                    '\nreturn get_request_headers_params.apply(null, args);\n'
                    '} finally { __xhsProbe.finish(); }\n}')
        self._patch(sign_util._xs_js, '_source', observed)
        real_call = sign_util._xs_js.call

        def call(identifier, *args):
            self.lock_observations.append(getattr(self.expected, 'sign_lock_held', False))
            if getattr(self.expected, 'same_process', False):
                return real_call('__same_process_sign', list(args),
                                 [self._input('a'), self._input('b'), self._input('b'), self._input('a')])
            return real_call('__observed_sign', list(args), self.expected.cookie,
                             getattr(self.expected, 'fail', False))
        self._patch(sign_util._xs_js, 'call', call)
        runtime = execjs.get('Node')
        self._patch(runtime, '_binary_cache', self.command)
        real_popen = external_runtime.Popen
        events = self.events
        command = self.command

        class OfflinePopen(real_popen):
            def __init__(self, args, **kwargs):
                if list(args) != command or kwargs.get('shell', False):
                    raise AssertionError('only guarded signing Node is allowed')
                kwargs['env'] = {'PATH': os.environ.get('PATH', '')}
                super().__init__(args, **kwargs)

            def communicate(self, *args, **kwargs):
                kwargs['timeout'] = 20
                try:
                    output, error = super().communicate(*args, **kwargs)
                except subprocess.TimeoutExpired:
                    self.kill()
                    super().communicate()
                    raise AssertionError('offline signing timed out') from None
                for line in (error or '').splitlines():
                    if line.startswith('XHS_SIGN_PROBE:'):
                        events.append(json.loads(line.split(':', 1)[1]))
                # Keep engine errors from including arguments in a test traceback.
                if output:
                    try:
                        result = json.loads(output.strip().splitlines()[-1])
                        if result[0] == 'err':
                            output = '\n["err","offline signing failed"]\n'
                    except (ValueError, IndexError):
                        output = '\n["err","offline signing failed"]\n'
                return output, ''
        self._patch(external_runtime, 'Popen', OfflinePopen)

    def _patch(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _input(self, char):
        return {'a1': char * 52, 'offline_flag': '1', 'synthetic': 'value=kept'}

    def _sign(self, cookie, *, fail=False):
        self.expected.cookie, self.expected.fail = cookie, fail
        text = '; '.join(key + '=' + value for key, value in cookie.items())
        with patch.object(random, 'random', return_value=0.25):
            return sign_util.generate_request_params(
                text, '/api/sns/web/v2/search/notes', {'keyword': 'synthetic test'}, 'POST',
            )

    def _assert_provenance(self, event, *, cleaned=True):
        self.assertTrue(event['initialInputMatches'], 'input must bind before initialization')
        self.assertTrue(event['cookieReadsMatch'], 'all real cookie reads must use this request')
        self.assertGreater(event['coreReads'], 0, 'real core must read its cookie context')
        self.assertTrue(event['coreExtractedInputA1'], 'real core must extract this request a1')
        self.assertTrue(event['commonMatches'], 'common must use the same input')
        if cleaned:
            self.assertTrue(event['authCleared'], 'normal and exceptional exits must clear auth')

    def test_real_chain_is_repeatable_and_a_b_a_has_no_context_leak(self):
        # Catches static initialization, an a1-only common fix, and stale auth.
        outputs = [self._sign(self._input(char)) for char in ('a', 'a', 'b', 'a')]
        self.assertEqual(len(self.events), 4)
        for event in self.events:
            self._assert_provenance(event)
        for index in (1, 3):
            self.assertTrue(outputs[0][0]['x-s'] == outputs[index][0]['x-s'])
            self.assertTrue(outputs[0][0]['x-s-common'] == outputs[index][0]['x-s-common'])
        for char, output in zip(('a', 'a', 'b', 'a'), outputs):
            self.assertTrue(output[1] == self._input(char), 'outgoing cookie must match input')
            self.assertTrue(output[2] == '{"keyword":"synthetic test"}')

    def test_exception_clears_auth_and_next_real_sign_is_independent(self):
        with self.assertRaises(execjs.ProgramError):
            self._sign(self._input('a'), fail=True)
        self._assert_provenance(self.events[-1])
        self._sign(self._input('b'))
        self._assert_provenance(self.events[-1])

    def test_clients_keep_real_signing_inside_existing_lock_and_http_outside(self):
        posted = []
        real_lock = xhs_service._XHS_SIGNING_LOCK
        state = self.expected
        class ObservedLock:
            def acquire(self):
                real_lock.acquire()
                state.sign_lock_held = True
            def release(self):
                state.sign_lock_held = False
                real_lock.release()
        def post(url, **kwargs):
            posted.append((not state.sign_lock_held, url, kwargs))
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {'success': True, 'data': {'items': []}})
        def request(char):
            cookie = self._input(char)
            self.expected.cookie = cookie
            client = xhs_service.XhsNativeClient('; '.join(k + '=' + v for k, v in cookie.items()))
            if char == 'a':
                client.search_notes('synthetic test')
            else:
                client.get_note_detail('synthetic-note', 'synthetic-token')
        with patch.object(xhs_service, '_XHS_SIGNING_LOCK', ObservedLock()), \
             patch.object(xhs_service.requests, 'post', side_effect=post), redirect_stdout(io.StringIO()):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(request, ('a', 'b')))
        self.assertTrue(all(self.lock_observations), 'context through common must be locked')
        self.assertEqual(len(self.events), 2)
        for event in self.events:
            self._assert_provenance(event)
        self.assertTrue(all(item[0] for item in posted), 'HTTP must remain outside signing lock')
        self.assertEqual(len(posted), 2)
        for _, url, request in posted:
            self.assertEqual(request['timeout'], 15)
            if '/search/' in url:
                self.assertEqual(request['headers']['authority'], 'so.xiaohongshu.com')
                self.assertIsInstance(request['data'], bytes)
            else:
                self.assertEqual(request['headers']['authority'], 'edith.xiaohongshu.com')
                self.assertIsInstance(request['data'], str)

    def test_real_client_failure_releases_lock_without_http_or_auth_residue(self):
        cookie = self._input('a')
        self.expected.cookie, self.expected.fail = cookie, True
        client = xhs_service.XhsNativeClient('; '.join(k + '=' + v for k, v in cookie.items()))
        with patch.object(xhs_service.requests, 'post') as post, redirect_stdout(io.StringIO()):
            with self.assertRaises(xhs_service.XHSRequestError) as raised:
                client.get_note_detail('synthetic-note')
        self.assertEqual(raised.exception.reason, 'sign_error')
        self.assertTrue(str(raised.exception) == 'XHS request signing failed')
        post.assert_not_called()
        self._assert_provenance(self.events[-1])
        acquired = xhs_service._XHS_SIGNING_LOCK.acquire(blocking=False)
        self.assertTrue(acquired, 'signing exception must release the existing lock')
        if acquired:
            xhs_service._XHS_SIGNING_LOCK.release()
        self._sign(self._input('b'))
        self._assert_provenance(self.events[-1])

    def test_initialization_exception_clears_cookie_before_any_core_call(self):
        source = sign_util._xs_js._source
        assignment = 'HTMLDocument.prototype.cookie = requestCookie;'
        self.assertTrue(assignment in source, 'probe must attach before core initialization')
        source = source.replace(assignment, assignment + '\nthrow new Error("offline initialization failure");')
        with patch.object(sign_util._xs_js, '_source', source), self.assertRaises(execjs.ProgramError):
            self._sign(self._input('a'))
        event = self.events[-1]
        self.assertTrue(event['initialInputMatches'])
        self.assertEqual(event['coreReads'], 0)
        self.assertFalse(event['commonMatches'])
        self.assertTrue(event['authCleared'])
        self._sign(self._input('b'))
        self._assert_provenance(self.events[-1])

    def test_same_node_a_failure_b_a_cannot_reuse_auth_context(self):
        # Exercise repeated exported entries in ONE Node, including a failure;
        # the usual production bridge starts a new Node for each invocation.
        sequence = r'''
function __same_process_sign(args, inputs) {
    const outputs = [];
    for (let i = 0; i < inputs.length; i++) {
        __xhsProbe.begin(inputs[i], i === 1);
        const callArgs = args.slice();
        callArgs[2] = inputs[i];
        try {
            const result = get_request_headers_params.apply(null, callArgs);
            outputs.push(result);
        } catch (error) {
            if (i !== 1) throw error;
        } finally { __xhsProbe.finish(); }
    }
    if (outputs.length !== 3 || outputs[0].xs !== outputs[2].xs ||
        outputs[0].xs_common !== outputs[2].xs_common) {
        throw new Error('same-process reproducibility failed');
    }
    return outputs[0];
}
'''
        self.expected.same_process = True
        with patch.object(sign_util._xs_js, '_source', self.instrumented_source + sequence):
            self._sign(self._input('a'))
        self.assertEqual(len(self.events), 4)
        for event in self.events:
            self._assert_provenance(event)


if __name__ == '__main__':
    from backend.tests.network_guard import guarded_unittest_main
    guarded_unittest_main()
