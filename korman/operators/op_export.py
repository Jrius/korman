#    This file is part of Korman.
#
#    Korman is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    Korman is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with Korman.  If not, see <http://www.gnu.org/licenses/>.

import bpy
from bpy.props import *
import cProfile
from pathlib import Path
import pstats

from .. import exporter
from ..properties.prop_world import PlasmaAge, game_versions
from ..korlib import ConsoleToggler

class ExportOperator(bpy.types.Operator):
    """Exports ages for Cyan Worlds' Plasma Engine"""

    bl_idname = "export.plasma_age"
    bl_label = "Export Age"

    # Here's the rub: we define the properties in this dict. We're going to register them as a seekrit
    # over on the PlasmaAge world properties. We've got a helper so we can access them like they're actually on us...
    # If you want a volatile property, register it directly on this operator!
    _properties = {
        "profile_export": (BoolProperty, {"name": "Profile",
                                          "description": "Profiles the exporter using cProfile",
                                          "default": False}),

        "verbose": (BoolProperty, {"name": "Display Verbose Log",
                                   "description": "Shows the verbose export log in the console",
                                   "default": False}),

        "show_console": (BoolProperty, {"name": "Display Log Console",
                                        "description": "Forces the Blender System Console open during the export",
                                        "default": True}),

        "texcache_path": (StringProperty, {"name": "Texture Cache Path",
                                           "description": "Texture Cache Filepath"}),

        "texcache_method": (EnumProperty, {"name": "Texture Cache",
                                           "description": "Texture Cache Settings",
                                           "items": [("skip", "Don't Use Texture Cache", "The texture cache is neither used nor updated."),
                                                     ("use", "Use Texture Cache", "Use (and update, if needed) cached textures."),
                                                     ("rebuild", "Rebuild Texture Cache", "Rebuilds the texture cache from scratch.")],
                                           "default": "use"}),

        "lighting_method": (EnumProperty, {"name": "Static Lighting",
                                           "description": "Static Lighting Settings",
                                           "items": [("skip", "Don't Bake Lighting", "Static lighting is not baked during this export (fastest export)"),
                                                     ("bake", "Bake Lighting", "Static lighting is baked according to your specifications"),
                                                     ("force_vcol", "Force Vertex Color Bake", "All static lighting is baked as vertex colors (faster export)"),
                                                     ("force_lightmap", "Force Lightmap Bake", "All static lighting is baked as lightmaps (slower export)")],
                                           "default": "bake"}),

        "export_active": (BoolProperty, {"name": "INTERNAL: Export currently running",
                                         "default": False,
                                         "options": {"SKIP_SAVE"}}),
    }

    # This wigs out and very bad things happen if it's not directly on the operator...
    filepath = StringProperty(subtype="FILE_PATH")
    filter_glob = StringProperty(default="*.age", options={'HIDDEN'})

    version = EnumProperty(name="Version",
                           description="Plasma version to export this age for",
                           items=game_versions,
                           default="pvPots",
                           options=set())

    def draw(self, context):
        layout = self.layout
        age = context.scene.world.plasma_age

        # The crazy mess we're doing with props on the fly means we have to explicitly draw them :(
        layout.prop(self, "version")
        layout.prop(age, "texcache_method", text="")
        layout.prop(age, "lighting_method")
        row = layout.row()
        row.enabled = ConsoleToggler.is_platform_supported()
        row.prop(age, "show_console")
        layout.prop(age, "verbose")
        layout.prop(age, "profile_export")

    def __getattr__(self, attr):
        if attr in self._properties:
            return getattr(bpy.context.scene.world.plasma_age, attr)
        raise AttributeError(attr)

    def __setattr__(self, attr, value):
        if attr in self._properties:
            setattr(bpy.context.scene.world.plasma_age, attr, value)
        else:
            super().__setattr__(attr, value)

    @property
    def has_reports(self):
        return hasattr(self.report)

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == "PLASMA_GAME"

    def execute(self, context):
        # Before we begin, do some basic sanity checking...
        path = Path(self.filepath)
        if not self.filepath:
            self.error = "No file specified"
            return {"CANCELLED"}
        else:
            if not path.exists:
                try:
                    path.mkdir(parents=True)
                except:
                    self.report({"ERROR"}, "Failed to create export directory")
                    return {"CANCELLED"}

        # We need to back out of edit mode--this ensures that all changes are committed
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        # Separate blender operator and actual export logic for my sanity
        ageName = path.stem
        with _UiHelper(context) as _ui:
            e = exporter.Exporter(self)
            try:
                self.export_active = True
                if self.profile_export:
                    profile = path.with_name("{}_cProfile".format(ageName))
                    profile = cProfile.runctx("e.run()", globals(), locals(), str(profile))
                else:
                    e.run()
            except exporter.ExportError as error:
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            else:
                if self.profile_export:
                    stats_out = path.with_name("{}_profile.log".format(ageName))
                    with open(str(stats_out), "w") as out:
                        stats = pstats.Stats(profile, stream=out)
                        stats = stats.sort_stats("time", "calls")
                        stats.print_stats()
                return {"FINISHED"}
            finally:
                self.export_active = False

    def invoke(self, context, event):
        # Called when a user hits "export" from the menu
        # We will prompt them for the export info, then call execute()
        if not self.filepath:
            blend_filepath = context.blend_data.filepath
            if not blend_filepath:
                blend_filepath = context.scene.world.plasma_age.age_name
            if not blend_filepath:
                blend_filepath = "Korman"
            self.filepath = str(Path(blend_filepath).with_suffix(".age"))
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    @classmethod
    def register(cls):
        # BEGIN MAJICK
        # Register the exporter properties such that they will persist
        for name, (prop, options) in cls._properties.items():
            # Hide these settings from being seen on the age properties
            age_options = dict(options)
            bl_options = age_options.setdefault("options", set())
            bl_options.add("HIDDEN")

            # Now do the majick
            setattr(PlasmaAge, name, prop(**age_options))


class _UiHelper:
    """This fun little helper makes sure that we don't wreck the UI"""
    def __init__(self, context):
        self.active_object = context.active_object
        self.selected_objects = context.selected_objects

    def __enter__(self):
        scene = bpy.context.scene
        self.layers = tuple(scene.layers)
        self.frame_num = scene.frame_current
        scene.frame_set(scene.frame_start)
        scene.update()

        # Some operators require there be an active_object even though they
        # don't actually use it...
        if scene.objects.active is None:
            scene.objects.active = scene.objects[0]
        return self

    def __exit__(self, type, value, traceback):
        for i in bpy.data.objects:
            i.select = (i in self.selected_objects)

        scene = bpy.context.scene
        scene.objects.active = self.active_object
        scene.layers = self.layers
        scene.frame_set(self.frame_num)
        scene.update()


# Add the export operator to the Export menu :)
def menu_cb(self, context):
    if context.scene.render.engine == "PLASMA_GAME":
        self.layout.operator_context = "INVOKE_DEFAULT"
        self.layout.operator(ExportOperator.bl_idname, text="Plasma Age (.age)")


def register():
    bpy.types.INFO_MT_file_export.append(menu_cb)

def unregister():
    bpy.types.INFO_MT_file_export.remove(menu_cb)
