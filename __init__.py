bl_info = {
    "name": "ROSE Online blender plugin",
    "author": "Ralph Minderhoud",
    "blender": (2, 77, 0),
    "version": (0, 0, 4),
    "location": "File > Import",
    "description": "Import files from ROSE Online",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    if "import_map" in locals():
        importlib.reload(import_map)
else:
    from .import_map import ImportMap
    from .import_zmd import ImportZMD
    from .import_zms import ImportZMS
    from .export_zms import ExportZMS
    from .import_zsc import ImportZSC

import bpy

def menu_func_export(self, context):
    self.layout.operator(ExportZMS.bl_idname, text="ROSE Mesh (.zms)")
    
def menu(self, context):
    self.layout.separator()
    self.layout.operator(ImportMap.bl_idname, text="ROSE Map (.zon)")
    self.layout.operator(ImportZMD.bl_idname, text=ImportZMD.bl_label)
    self.layout.operator(ImportZMS.bl_idname, text=ImportZMS.bl_label)

def register():
    bpy.utils.register_class(ImportMap)
    bpy.utils.register_class(ImportZMD)
    bpy.utils.register_class(ImportZMS)
    bpy.utils.register_class(ExportZMS)
    bpy.types.TOPBAR_MT_file_import.append(menu)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.utils.register_class(ImportZSC)

def unregister():
    bpy.utils.unregister_class(ImportMap)
    bpy.utils.unregister_class(ImportZMD)
    bpy.utils.unregister_class(ImportZMS)
    bpy.utils.unregister_class(ExportZMS)
    bpy.types.TOPBAR_MT_file_import.remove(menu)
    bpy.utils.unregister_class(ImportZSC)

if __name__ == "__main__":
    register()
