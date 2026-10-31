import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location('local_server', Path(__file__).resolve().parents[1] / 'app/server.py')
server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)

class Backend:
    def __init__(self): self.calls=[]
    def auth(self): self.calls.append('auth'); return 'a' * 64
    def book(self, task, account, **kwargs): self.calls.append(('book', task, account)); return {'ok':True, 'verified':True, 'state':'success'}
    def availability(self, args): self.calls.append(('availability', args)); return {'ok': True, 'slots': []}

class DispatchTests(unittest.TestCase):
    def test_auth_pin_and_booking_selected_task(self):
        backend=Backend(); app=server.Bridge(backend)
        task={'sport':'健身房','venue':'翔安校区健身房','target_date':'2026-10-29','target_time_start':'13:30','target_time_end':'15:00','allow_time_fallback':False}
        app.invoke('save_task', {'task':task})
        with self.assertRaisesRegex(ValueError, '账号'): app.invoke('start_http_run', {'until':'submit'})
        app.invoke('start_http_run', {'until':'auth'})
        app.invoke('start_http_run', {'until':'submit'})
        self.assertEqual(backend.calls[-1], ('book',task,'a'*64))
    def test_failed_reauthentication_revokes_previous_pin(self):
        backend=Backend(); app=server.Bridge(backend)
        app.invoke('start_http_run', {'until':'auth'})
        def fail(): raise ValueError('expired')
        backend.auth=fail
        with self.assertRaises(ValueError): app.invoke('start_http_run', {'until':'auth'})
        app.invoke('save_task', {'task':{'allow_time_fallback':False}})
        with self.assertRaisesRegex(ValueError, '账号'): app.invoke('start_http_run', {'until':'submit'})
    def test_unverified_submission_is_not_reported_as_success(self):
        backend=Backend(); backend.book=lambda task, account: {'ok':True,'verified':False,'state':'submitted'}
        app=server.Bridge(backend); app.invoke('save_task',{'task':{'allow_time_fallback':False}})
        app.invoke('start_http_run', {'until':'auth'})
        with self.assertRaisesRegex(ValueError,'未核实'): app.invoke('start_http_run', {'until':'submit'})
    def test_verified_upstream_result_is_a_completed_live_event(self):
        backend=Backend()
        backend.book=lambda task, account: {'ok':True,'state':'submitted','verified':True,'order_id':123,'time':'19:30-21:00','court':'01'}
        app=server.Bridge(backend); app.invoke('save_task',{'task':{'task_id':'t','allow_time_fallback':False}})
        app.invoke('start_http_run', {'until':'auth'})
        self.assertEqual(app.invoke('start_http_run', {'until':'submit'}), {'ok':True,'verified':True})
        event=app.invoke('get_live_status',{})['last_event']
        self.assertEqual(event.get('action'), 'done')
        self.assertEqual(event['http']['verified'], True)
    def test_scheduler_and_unknown_are_errors(self):
        app=server.Bridge(Backend())
        for name in ('arm_hang','random_unknown'):
            with self.assertRaises(ValueError): app.invoke(name,{})
    def test_availability_delegates_and_save_roundtrip(self):
        backend=Backend(); app=server.Bridge(backend)
        app.invoke('save_task',{'task':{'sport':'健身房'}})
        self.assertEqual(app.invoke('read_task_raw',{})['sport'],'健身房')
        args={'sport':'健身房','venue':'翔安校区健身房','date':'2026-10-29'}
        self.assertEqual(app.invoke('get_venue_availability',args)['slots'],[])
        self.assertEqual(backend.calls[-1], ('availability',args))
    def test_fallback_cannot_silently_be_ignored(self):
        app=server.Bridge(Backend()); app.invoke('save_task',{'task':{'allow_time_fallback':True}})
        app.invoke('start_http_run', {'until':'auth'})
        with self.assertRaisesRegex(ValueError,'备选'): app.invoke('start_http_run', {'until':'submit'})
