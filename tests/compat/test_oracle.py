import importlib.util, pathlib, unittest
p=pathlib.Path(__file__).with_name('oracle.py')
spec=importlib.util.spec_from_file_location('oracle',p); oracle=importlib.util.module_from_spec(spec); spec.loader.exec_module(oracle)
class ComparatorTests(unittest.TestCase):
 def test_line_endings_and_record_order_are_normalized(self):
  a=b'TN:x\nSF:a.c\nDA:2,1\nDA:1,0\nFNL:0,1\nFNA:0,2,alias\nend_of_record\n'
  b=b'TN:x\r\nSF:a.c\r\nFNL:0,1\r\nFNA:0,2,alias\r\nDA:1,0\r\nDA:2,1\r\nend_of_record\r\n'
  self.assertEqual(oracle.normalized(a),oracle.normalized(b))
 def test_testcase_identity_is_not_erased(self):
  self.assertNotEqual(oracle.normalized(b'TN:a\nSF:x\nDA:1,1\nend_of_record\n'),oracle.normalized(b'TN:b\nSF:x\nDA:1,1\nend_of_record\n'))
 def test_modern_alias_is_not_erased(self):
  self.assertNotEqual(oracle.normalized(b'TN:a\nSF:x\nFNL:0,1\nFNA:0,1,first\nend_of_record\n'),oracle.normalized(b'TN:a\nSF:x\nFNL:0,1\nFNA:0,1,second\nend_of_record\n'))
 def test_function_alias_order_is_preserved(self):
  a=b'TN:a\nSF:x\nFNL:0,1\nFNA:0,1,first\nFNA:0,1,second\nend_of_record\n'
  b=b'TN:a\nSF:x\nFNL:0,1\nFNA:0,1,second\nFNA:0,1,first\nend_of_record\n'
  self.assertNotEqual(oracle.normalized(a),oracle.normalized(b))
 def test_mcdc_identity_is_not_erased(self):
  self.assertNotEqual(oracle.normalized(b'TN:a\nSF:x\nMCDC:2,2,t,1,0,A\nend_of_record\n'),oracle.normalized(b'TN:a\nSF:x\nMCDC:2,2,t,1,0,B\nend_of_record\n'))
 def test_checksum_is_not_erased(self):
  self.assertNotEqual(oracle.normalized(b'TN:a\nSF:x\nDA:1,1,abc\nend_of_record\n'),oracle.normalized(b'TN:a\nSF:x\nDA:1,1,def\nend_of_record\n'))
 def test_deliberately_wrong_coverage_records_fail_comparison(self):
  expected=(b'TN:a\nSF:x\nVER:2.6\nDA:1,2,abc\nBRDA:1,4,2,1\nFNL:0,1,1\nFNA:0,2,alias\nMCDC:1,2,t,1,0,expr\nLF:1\nLH:1\nend_of_record\n')
  wrong_records=((b'DA:1,2,abc',b'DA:1,3,abc'),(b'BRDA:1,4,2,1',b'BRDA:1,4,2,0'),(b'FNA:0,2,alias',b'FNA:0,2,other'),(b'MCDC:1,2,t,1,0,expr',b'MCDC:1,2,t,1,0,other'),(b'LF:1',b'LF:2'),(b'VER:2.6',b'VER:2.7'))
  for original,wrong in wrong_records:
   with self.subTest(wrong=wrong):
    self.assertNotEqual(oracle.normalized(expected),oracle.normalized(expected.replace(original,wrong)))
 def test_section_order_is_preserved(self):
  a=b'TN:a\nSF:x\nDA:1,1\nend_of_record\nTN:b\nSF:x\nDA:1,2\nend_of_record\n'
  self.assertNotEqual(oracle.normalized(a),oracle.normalized(b''.join(reversed(a.splitlines(keepends=True)))))
if __name__=='__main__': unittest.main()
