import argparse
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

import import_bonap as bonap


class BonapTests(unittest.TestCase):
    def test_names_and_infraspecific_union_targets(self):
        for name in ['Acer rubrum', 'Acer rubrum var. drummondii', 'Acer rubrum subsp. trilobum',
                     'Acer rubrum ssp. trilobum var. drummondii']:
            self.assertEqual(bonap.parent_name('Acer', name), ('Acer', 'rubrum'))
        self.assertEqual(bonap.parent_name('Salix', 'Salix × sepulcralis'), ('Salix', 'x sepulcralis'))
        with self.assertRaises(ValueError):
            bonap.parent_name('Acer', "Acer rubrum 'Cultivar'")

    def test_payload_preserves_raw_without_inventing_traits(self):
        row = dict.fromkeys(bonap.META, '')
        row.update(Genus='Acer', Species='Acer rubrum var. drummondii', Duration='Annual, Perennial',
                   Habit='Tree', Habitat='Wetland plant | Wetland plant',
                   **{'Flowering Season':'Spring flowering; Summer flowering', 'One':'1', 'Two':'0'})
        payload = bonap.payload_for(row, ['One','Two'], {'One':(1001,'Autauga'),'Two':(1003,'Baldwin')},'hash')
        self.assertEqual(payload['plant']['species'],'rubrum')
        self.assertNotIn('life_cycle',payload['plant'])
        self.assertNotIn('water',payload['plant'])
        self.assertEqual(payload['plant']['misc'],['Wetland plant'])
        self.assertEqual(payload['counties'],[{'fips':1001,'name':'Autauga'}])
        self.assertEqual(payload['bonap']['bloom_seasons'],['Spring','Summer'])
        self.assertEqual(payload['bonap']['raw']['Duration'],'Annual, Perennial')
        row['One']='unknown'
        with self.assertRaisesRegex(ValueError,'Invalid county presence'):
            bonap.payload_for(row,['One'],{},'hash')

    def test_ambiguous_mapping_requires_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'counties.csv'
            p.write_text('county,state,fips\nBaltimore,Maryland,24005\nBaltimore,Maryland,24510\n')
            mapping, ambiguous=bonap.county_mapping(p,{})
            self.assertEqual(mapping,{})
            self.assertEqual(ambiguous,{'Baltimore, Maryland':[24005,24510]})
            self.assertEqual(bonap.county_mapping(p,{'Baltimore, Maryland':24510})[0]['Baltimore, Maryland'][0],24510)
            with self.assertRaises(ValueError):
                bonap.county_mapping(p,{'Baltimore, Maryland':12345})

    def test_confirmed_only_resume_context_and_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            (directory/'plan.jsonl').write_text(json.dumps({'plant':{'genus':'Acer','species':'rubrum'}})+'\n')
            db=MagicMock(project_ref='a'*20)
            db.query.return_value=[{'result':{'success':True,'count':0,'plant_ids':[]}}]
            with self.assertRaisesRegex(RuntimeError,'not confirmed'):
                bonap.apply_plan(db,directory,10)
            self.assertEqual((directory/'progress.jsonl').read_text(),'')
            db.query.return_value=[{'result':{'success':True,'count':1,'plant_ids':[7]}}]
            bonap.apply_plan(db,directory,10)
            calls=db.query.call_count
            bonap.apply_plan(db,directory,10)
            self.assertEqual(db.query.call_count,calls)
            db.project_ref='b'*20
            bonap.apply_plan(db,directory,10)
            self.assertEqual(db.query.call_count,calls+1)


if __name__=='__main__':
    unittest.main()
