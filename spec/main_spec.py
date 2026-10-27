'''
tetron-packaging main spec
'''

from libspec import Spec
from . import product, mkdeb, action, constraints

class MainSpec(Spec):
    def modules(self):
        return [product, mkdeb, action, constraints]
