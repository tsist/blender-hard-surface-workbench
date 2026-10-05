"""Hard Surface Workbench. Import has no registration or global configuration effects."""
__version__='0.2.0'
bl_info={'name':'Hard Surface Workbench','author':'OpenAI development assistant','version':(0,2,0),'blender':(5,2,0),'location':'View3D > Sidebar > Hard Surface','description':'Bounded candidate-only mechanical mesh work units','category':'Mesh'}

def register():
    from . import ui
    ui.register()

def unregister():
    from . import ui
    ui.unregister()
