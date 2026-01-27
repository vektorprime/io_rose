import sys
sys.path.insert(0, r'C:\Users\vicha\Downloads\rose129\client\3Ddata\MAPS\JUNON\JDT01')

from rose.zon import Zon

z = Zon(r'C:\Users\vicha\Downloads\rose129\client\3Ddata\MAPS\JUNON\JDT01\JDT01.ZON')
print('Textures:', z.textures)
print('Tile count:', len(z.tiles))
if z.tiles:
    print('First tile:', z.tiles[0])