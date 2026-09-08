import sys, traceback

# Forzar salida de errores visible
import builtins
_print = builtins.print

try:
    import mediavault__8__ as mv
except ModuleNotFoundError:
    # Si el nombre tiene espacios, intentar importar directamente
    pass

# Parchear _silenciar para que no suprima stderr
import os
_orig_dup2 = os.dup2
def _dup2_noop(*a, **kw): pass

try:
    import importlib.util, pathlib
    # Buscar el archivo en la misma carpeta
    spec = importlib.util.spec_from_file_location(
        "mediavault",
        pathlib.Path(__file__).parent / "mediavault (8).py"
    )
    mod = importlib.util.module_from_spec(spec)
    
    # Parchear os.dup2 antes de cargar para que no silencie stderr
    os.dup2 = _dup2_noop
    spec.loader.exec_module(mod)
    os.dup2 = _orig_dup2
    
    app = mod.MediaVault()
    app.mainloop()

except Exception as e:
    os.dup2 = _orig_dup2
    print("=" * 60)
    print("ERROR AL INICIAR MEDIAVAULT:")
    print("=" * 60)
    traceback.print_exc()
    print("=" * 60)
    input("Presiona ENTER para cerrar...")