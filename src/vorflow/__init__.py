from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("vorflow")
except PackageNotFoundError:
    # Package is not installed (e.g. running from source)
    __version__ = "0.0.2" 
    
from .blueprint import ConceptualMesh
from .engine import MeshGenerator
from .tessellator import VoronoiTessellator
from .fields import (MeshField, ThresholdField,
                     ExponentialField, AutoLinearField,
                       AutoExponentialField)

__all__ = ["ConceptualMesh", "MeshGenerator", "VoronoiTessellator",
           "MeshField", "ThresholdField", "ExponentialField",
           "AutoLinearField", "AutoExponentialField"]