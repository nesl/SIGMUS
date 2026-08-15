import unittest

from kg_construction import ontology_classes


class OntologySmokeTest(unittest.TestCase):
    def test_core_ontology_module_imports(self):
        public_names = [name for name in vars(ontology_classes) if not name.startswith("_")]
        self.assertTrue(public_names)


if __name__ == "__main__":
    unittest.main()
