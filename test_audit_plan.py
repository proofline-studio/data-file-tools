import json,pathlib,unittest
class PlanTests(unittest.TestCase):
 def test_hire_first_read_only_scope(self):
  p=json.loads(pathlib.Path(__file__).with_name('audit-plan.json').read_text(encoding='utf-8'))
  self.assertEqual(p['task_id'],'cmv11tua3007606jj2ne1mbwc')
  self.assertEqual(p['identity'],'proofline-studio-dail')
  self.assertEqual(p['payout'],{'amount':'1','asset':'USDC','network':'base','required_prefunding':False})
  self.assertEqual(p['specification']['parameters']['service_mode'],'read_only_catalog')
  self.assertIn('No purchase or transfer',p['exclusions'])
  self.assertIn('No financial signature',p['exclusions'])
  self.assertIn('No bounty claim',p['exclusions'])
  self.assertIn('No service advertisement',p['exclusions'])
  self.assertIn('never blindly repeats',p['registration'])
if __name__=='__main__':unittest.main()
