# introspect_tspack.py
import importlib
from pathlib import Path
import site
import sys

print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")
print("\nSite packages paths:")
for p in site.getsitepackages():
    print(f"  {p}")

package_name = "tree_sitter_language_pack"
print(f"\nAttempting to import top-level '{package_name}'...")

try:
    ts_pack = importlib.import_module(package_name)
    print(f"Successfully imported '{package_name}'. Location: {getattr(ts_pack, '__file__', 'N/A')}")
    print(f"\nAttributes/submodules directly available under '{package_name}':")
    
    count = 0
    for attr_name in dir(ts_pack):
        if not attr_name.startswith("_"): # Filter out private/dunder attributes
            print(f"  - {attr_name}")
            
            # Try to see if these attributes are modules themselves and have a 'language' callable
            try:
                sub_module_maybe = getattr(ts_pack, attr_name)
                if importlib.util.find_spec(f"{package_name}.{attr_name}"): # Check if it's a submodule
                    print(f"    Trying to import {package_name}.{attr_name}.{attr_name} as per Goldziher structure...")
                    try:
                        # Test Goldziher's specific import structure
                        # e.g. tree_sitter_language_pack.python.python
                        specific_lang_module_path = f"{package_name}.{attr_name}.{attr_name}"
                        specific_lang_module = importlib.import_module(specific_lang_module_path)
                        if hasattr(specific_lang_module, 'language') and callable(specific_lang_module.language):
                            print(f"      SUCCESS: Found callable 'language' in {specific_lang_module_path}")
                        else:
                            print(f"      WARNING: No callable 'language' in {specific_lang_module_path}")
                    except ImportError:
                        print(f"      INFO: Module {specific_lang_module_path} not found.")
                    except Exception as e_specific:
                        print(f"      ERROR inspecting {specific_lang_module_path}: {e_specific}")

            except Exception as e_attr:
                print(f"    Error inspecting attribute {attr_name}: {e_attr}")
            count += 1
    if count == 0:
        print(f"  (No public attributes/submodules found in '{package_name}')")

except ImportError:
    print(f"Failed to import the top-level package '{package_name}'. Ensure it's installed correctly.")
except Exception as e:
    print(f"An error occurred: {e}")

print("\n--- Alternative Check: Using pkgutil to list submodules ---")
try:
    import pkgutil
    # Temporarily add site-packages to sys.path if not already (might be needed for pkgutil in some envs)
    # For pkgutil.iter_modules to find tree_sitter_language_pack, its parent must be on sys.path
    # Assuming ts_pack.__file__ gave us something like .../site-packages/tree_sitter_language_pack/__init__.py
    if 'ts_pack' in locals() and hasattr(ts_pack, '__file__') and ts_pack.__file__:
        package_location = str(Path(ts_pack.__file__).parent.parent) # up to site-packages
        if package_location not in sys.path:
            sys.path.insert(0, package_location)
            print(f"Temporarily added to sys.path: {package_location}")
    
    print(f"Searching for submodules of '{package_name}' using pkgutil...")
    # Re-import to ensure pkgutil sees it if path was just added
    if 'ts_pack' not in locals(): ts_pack = importlib.import_module(package_name)

    if hasattr(ts_pack, '__path__'):
        for sub_info in pkgutil.iter_modules(ts_pack.__path__, ts_pack.__name__ + "."):
            print(f"  Found by pkgutil: name={sub_info.name}, ispkg={sub_info.ispkg}")
            if not sub_info.ispkg and sub_info.name.count('.') == 2 : # e.g. tree_sitter_language_pack.python.python
                try:
                    final_module = importlib.import_module(sub_info.name)
                    if hasattr(final_module, 'language') and callable(final_module.language):
                        print(f"    >>> SUCCESS: {sub_info.name} has callable 'language'")
                    else:
                        print(f"    >>> WARNING: {sub_info.name} does NOT have callable 'language'")
                except Exception as e_sub:
                    print(f"    Error importing/inspecting {sub_info.name}: {e_sub}")
    else:
        print(f"'{package_name}' does not seem to be a package with __path__ (it's a module).")

except ImportError:
    print(f"Could not import '{package_name}' for pkgutil introspection.")
except Exception as e:
    print(f"An error occurred during pkgutil introspection: {e}")