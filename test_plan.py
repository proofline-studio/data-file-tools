import json,pathlib,unittest
class PlanTest(unittest.TestCase):
 def test_exact_assessment_terms(self):
  p=json.loads(pathlib.Path(__file__).with_name('assessment-plan.json').read_text())
  self.assertEqual(p['task_id'],'cmv3yauqc01kr06jmt8beq4h9')
  self.assertEqual(p['identity'],'proofline-studio-dail')
  self.assertEqual(p['specification']['parameters']['service_mode'],'assessment_only')
  self.assertEqual(p['payout'],{'amount':'1','asset':'USDC','network':'base','required_prefunding':False})
  self.assertIn('No challenge enrollment',p['exclusions'])
  self.assertIn('No spending or transfer',p['exclusions'])
if __name__=='__main__':unittest.main()
