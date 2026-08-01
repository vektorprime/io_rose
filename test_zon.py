import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests"))

from tests import _paths
from rose.zon import Zon

ZONE_DIR = os.environ.get("ROSE_TEST_ZONE", _paths.client_zone_dir())
ZONE_FILE = os.path.join(ZONE_DIR, "JDT01.ZON")

z = Zon(ZONE_FILE)
print("Textures:", z.textures)
print("Tile count:", len(z.tiles))
if z.tiles:
    print("First tile:", z.tiles[0])
