bl_info = {
    "name": "ROSE Online blender plugin",
    "author": "Ralph Minderhoud and Ryko",
    "blender": (2, 77, 0),
    "version": (0, 0, 7),
    "location": "File > Import",
    "description": "Import files from ROSE Online",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    # Reload submodule objects (the old checks tested class names like
    # "import_map" against locals, which never match, so F8/re-enable kept
    # stale code). Parsers first, operators after.
    from .rose import dds, zms, zmd, zmo, zsc, zon, ifo, til, him, eft, ptl
    importlib.reload(dds)
    importlib.reload(zms)
    importlib.reload(zmd)
    importlib.reload(zmo)
    importlib.reload(zsc)
    importlib.reload(zon)
    importlib.reload(ifo)
    importlib.reload(til)
    importlib.reload(him)
    importlib.reload(eft)
    importlib.reload(ptl)
    from . import (import_map, import_terrain, import_converted_terrain,
                   import_combined_zone, import_zsc, import_zms, import_zmd,
                   import_zmo, import_zms_zmd, export_zms, export_zmo,
                   enhance_wings, export_zone, import_eft, export_eft)
    importlib.reload(import_map)
    importlib.reload(import_terrain)
    importlib.reload(import_converted_terrain)
    importlib.reload(import_combined_zone)
    importlib.reload(import_zsc)
    importlib.reload(import_zms)
    importlib.reload(import_zmd)
    importlib.reload(import_zmo)
    importlib.reload(import_zms_zmd)
    importlib.reload(export_zms)
    importlib.reload(export_zmo)
    importlib.reload(enhance_wings)
    importlib.reload(export_zone)
    importlib.reload(import_eft)
    importlib.reload(export_eft)
else:
    from .import_map import ImportMap
    from .import_terrain import ImportTerrain
    from .import_converted_terrain import ImportConvertedTerrain
    from .import_combined_zone import ImportCombinedZone
    from .import_zmd import ImportZMD
    from .import_zms import ImportZMS
    from .export_zms import ExportZMS
    from .export_zmo import ExportZMO
    from .import_zsc import ImportZSC
    from .enhance_wings import EnhanceWings
    from .import_zmo import ImportZMO
    from .import_zms_zmd import ImportZMSwithZMD
    from .export_zone import ExportZone, AddZoneObject, MarkZoneObjectDeleted
    from .import_eft import ImportEFT
    from .export_eft import ExportEFT

import bpy

def menu_func_export(self, context):
    self.layout.operator(ExportZMS.bl_idname, text="ROSE Mesh (.zms)")
    self.layout.operator(ExportZMO.bl_idname, text="ROSE Animation (.zmo)")
    self.layout.operator(ExportEFT.bl_idname, text="ROSE Effect (.eft)")
    self.layout.operator(ExportZone.bl_idname, text="ROSE Zone (.zon) - Save Edited Zone")
    self.layout.operator(AddZoneObject.bl_idname, text="ROSE Object - Add Selected Mesh to Zone")
    
def menu(self, context):
    self.layout.separator()
    self.layout.operator(ImportCombinedZone.bl_idname, text="ROSE Zone (Converted Terrain + Assets)")
    self.layout.operator(ImportMap.bl_idname, text="ROSE Map (.zon)")
    self.layout.operator(ImportTerrain.bl_idname, text="ROSE Terrain Only (.zon)")
    self.layout.operator(ImportConvertedTerrain.bl_idname, text="Converted ROSE Terrain (.mesh.bin)")
    self.layout.operator(ImportZSC.bl_idname, text="ROSE Scene (.zsc)")
    self.layout.operator(ImportZMD.bl_idname, text=ImportZMD.bl_label)
    self.layout.operator(ImportZMS.bl_idname, text=ImportZMS.bl_label)
    self.layout.operator(ImportZMSwithZMD.bl_idname, text=ImportZMSwithZMD.bl_label)
    self.layout.operator(ImportZMO.bl_idname, text=ImportZMO.bl_label)
    self.layout.operator(ImportEFT.bl_idname, text=ImportEFT.bl_label)
    self.layout.operator(EnhanceWings.bl_idname, text="ROSE Wings Enhancer (batch)")

def register():
    bpy.utils.register_class(ImportCombinedZone)
    bpy.utils.register_class(ImportMap)
    bpy.utils.register_class(ImportTerrain)
    bpy.utils.register_class(ImportConvertedTerrain)
    bpy.utils.register_class(ImportZMD)
    bpy.utils.register_class(ImportZMS)
    bpy.utils.register_class(ExportZMS)
    bpy.utils.register_class(ExportZMO)
    bpy.utils.register_class(ImportZMO)
    bpy.utils.register_class(ImportZMSwithZMD)
    bpy.utils.register_class(ImportEFT)
    bpy.utils.register_class(ExportEFT)
    try:
        from .import_eft import register_quad_sync
        register_quad_sync()
    except Exception:
        pass
    bpy.utils.register_class(ExportZone)
    bpy.utils.register_class(AddZoneObject)
    bpy.utils.register_class(MarkZoneObjectDeleted)
    bpy.types.TOPBAR_MT_file_import.append(menu)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.utils.register_class(ImportZSC)
    bpy.utils.register_class(EnhanceWings)

def unregister():
    bpy.utils.unregister_class(ImportCombinedZone)
    bpy.utils.unregister_class(ImportMap)
    bpy.utils.unregister_class(ImportTerrain)
    bpy.utils.unregister_class(ImportConvertedTerrain)
    bpy.utils.unregister_class(ImportZMD)
    bpy.utils.unregister_class(ImportZMS)
    bpy.utils.unregister_class(ExportZMS)
    bpy.utils.unregister_class(ExportZMO)
    bpy.utils.unregister_class(ImportZMO)
    bpy.utils.unregister_class(ImportZMSwithZMD)
    bpy.utils.unregister_class(ImportEFT)
    bpy.utils.unregister_class(ExportEFT)
    try:
        from .import_eft import unregister_quad_sync
        unregister_quad_sync()
    except Exception:
        pass
    bpy.utils.unregister_class(ExportZone)
    bpy.utils.unregister_class(AddZoneObject)
    bpy.utils.unregister_class(MarkZoneObjectDeleted)
    bpy.types.TOPBAR_MT_file_import.remove(menu)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(ImportZSC)
    bpy.utils.unregister_class(EnhanceWings)

if __name__ == "__main__":
    register()
