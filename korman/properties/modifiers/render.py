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
from PyHSPlasma import *

from .base import PlasmaModifierProperties
from ...exporter import utils
from ...exporter.explosions import ExportError

class PlasmaFollowMod(PlasmaModifierProperties):
    pl_id = "followmod"

    bl_category = "Render"
    bl_label = "Follow"
    bl_description = "Follow the movement of the camera, player, or another object"

    follow_mode = EnumProperty(name="Mode",
                               description="Leader's movement to follow",
                               items=[
                                      ("kPositionX", "X Axis", "Follow the leader's X movements"),
                                      ("kPositionY", "Y Axis", "Follow the leader's Y movements"),
                                      ("kPositionZ", "Z Axis", "Follow the leader's Z movements"),
                                      ("kRotate", "Rotation", "Follow the leader's rotation movements"),
                                ],
                               default={"kPositionX", "kPositionY", "kPositionZ"},
                               options={"ENUM_FLAG"})

    leader_type = EnumProperty(name="Leader Type",
                               description="Leader to follow",
                               items=[
                                      ("kFollowCamera", "Camera", "Follow the camera"),
                                      ("kFollowListener", "Listener", "Follow listeners"),
                                      ("kFollowPlayer", "Player", "Follow the local player"),
                                      ("kFollowObject", "Object", "Follow an object"),
                                ])

    leader_object = StringProperty(name="Leader Object",
                                   description="Object to follow")

    def export(self, exporter, bo, so):
        fm = exporter.mgr.find_create_object(plFollowMod, so=so, name=self.key_name)

        fm.mode = 0
        for flag in (getattr(plFollowMod, mode) for mode in self.follow_mode):
            fm.mode |= flag

        fm.leaderType = getattr(plFollowMod, self.leader_type)
        if self.leader_type == "kFollowObject":
            # If this object is following another object, make sure that the
            # leader has been selected and is a valid SO.
            if self.leader_object:
                leader_obj = bpy.data.objects.get(self.leader_object, None)
                if leader_obj is None:
                    raise ExportError("'{}': Follow's leader object is invalid".format(self.key_name))
                else:
                    fm.leader = exporter.mgr.find_create_key(plSceneObject, bl=leader_obj)
            else:
                raise ExportError("'{}': Follow's leader object must be selected".format(self.key_name))

    @property
    def requires_actor(self):
        return True


class PlasmaLightMapGen(PlasmaModifierProperties):
    pl_id = "lightmap"

    bl_category = "Render"
    bl_label = "Lightmap"
    bl_description = "Auto-Bake Lightmap"

    quality = EnumProperty(name="Quality",
                           description="Resolution of lightmap",
                           items=[
                                  ("128", "128px", "128x128 pixels"),
                                  ("256", "256px", "256x256 pixels"),
                                  ("512", "512px", "512x512 pixels"),
                                  ("1024", "1024px", "1024x1024 pixels"),
                            ])

    light_group = StringProperty(name="Light Group",
                                 description="Group that defines the collection of lights to bake")

    uv_map = StringProperty(name="UV Texture",
                            description="UV Texture used as the basis for the lightmap")

    def export(self, exporter, bo, so):
        mat_mgr = exporter.mesh.material
        materials = mat_mgr.get_materials(bo)
        lightmap_im = bpy.data.images.get("{}_LIGHTMAPGEN.png".format(bo.name))

        # Find the stupid UVTex
        uvw_src = 0
        for i, uvtex in enumerate(bo.data.tessface_uv_textures):
            if uvtex.name == "LIGHTMAPGEN":
                uvw_src = i
                break
        else:
            # TODO: raise exception
            pass

        for matKey in materials:
            layer = exporter.mgr.add_object(plLayer, name="{}_LIGHTMAPGEN".format(matKey.name), so=so)
            layer.UVWSrc = uvw_src

            # Colors science'd from PRPs
            layer.ambient = hsColorRGBA(1.0, 1.0, 1.0)
            layer.preshade = hsColorRGBA(0.5, 0.5, 0.5)
            layer.runtime = hsColorRGBA(0.5, 0.5, 0.5)

            # GMatState
            gstate = layer.state
            gstate.blendFlags |= hsGMatState.kBlendMult
            gstate.clampFlags |= (hsGMatState.kClampTextureU | hsGMatState.kClampTextureV)
            gstate.ZFlags |= hsGMatState.kZNoZWrite
            gstate.miscFlags |= hsGMatState.kMiscLightMap

            mat = matKey.object
            mat.compFlags |= hsGMaterial.kCompIsLightMapped
            mat.addPiggyBack(layer.key)

            # Mmm... cheating
            mat_mgr.export_prepared_layer(layer, lightmap_im)

    @property
    def key_name(self):
        return "{}_LIGHTMAPGEN".format(self.id_data.name)

    @property
    def resolution(self):
        return int(self.quality)

class PlasmaViewFaceMod(PlasmaModifierProperties):
    pl_id = "viewfacemod"

    bl_category = "Render"
    bl_label = "Swivel"
    bl_description = "Swivel object to face the camera, player, or another object"

    preset_options = EnumProperty(name="Type",
                                  description="Type of Facing",
                                  items=[
                                         ("Billboard", "Billboard", "Face the camera (Y Axis only)"),
                                         ("Sprite", "Sprite", "Face the camera (All Axis)"),
                                         ("Custom", "Custom", "Custom Swivel"),
                                   ])

    follow_mode = EnumProperty(name="Target Type",
                               description="Target of the swivel",
                               items=[
                                      ("kFaceCam", "Camera", "Face the camera"),
                                      ("kFaceList", "Listener", "Face listeners"),
                                      ("kFacePlay", "Player", "Face the local player"),
                                      ("kFaceObj", "Object", "Face an object"),
                                ])
    target_object = StringProperty(name="Target Object",
                                   description="Object to face")

    pivot_on_y = BoolProperty(name="Pivot on local Y",
                              description="Swivel only around the local Y axis",
                              default=False)

    offset = BoolProperty(name="Offset", description="Use offset vector", default=False)
    offset_local = BoolProperty(name="Local", description="Use local coordinates", default=False)
    offset_coord = FloatVectorProperty(name="", subtype="XYZ")

    def export(self, exporter, bo, so):
        vfm = exporter.mgr.find_create_object(plViewFaceModifier, so=so, name=self.key_name)

        # Set a default scaling (libHSPlasma will set this to 0 otherwise).
        vfm.scale = hsVector3(1,1,1)
        l2p = utils.matrix44(bo.matrix_local)
        vfm.localToParent = l2p
        vfm.parentToLocal = l2p.inverse()

        # Cyan has these as separate components, but they're really just preset
        # options for common swivels.  We've consolidated them both here, along
        # with the fully-customizable swivel as a third option.
        if self.preset_options == "Billboard":
            vfm.setFlag(plViewFaceModifier.kFaceCam, True)
            vfm.setFlag(plViewFaceModifier.kPivotY, True)
        elif self.preset_options == "Sprite":
            vfm.setFlag(plViewFaceModifier.kFaceCam, True)
            vfm.setFlag(plViewFaceModifier.kPivotFace, True)
        elif self.preset_options == "Custom":
            # For the discerning artist, full control over their swivel options!
            vfm.setFlag(getattr(plViewFaceModifier, self.follow_mode), True)

            if self.follow_mode == "kFaceObj":
                # If this swivel is following an object, make sure that the
                # target has been selected and is a valid SO.
                if self.target_object:
                    target_obj = bpy.data.objects.get(self.target_object, None)
                    if target_obj is None:
                        raise ExportError("'{}': Swivel's target object is invalid".format(self.key_name))
                    else:
                        vfm.faceObj = exporter.mgr.find_create_key(plSceneObject, bl=target_obj)
                else:
                    raise ExportError("'{}': Swivel's target object must be selected".format(self.key_name))

            if self.pivot_on_y:
                vfm.setFlag(plViewFaceModifier.kPivotY, True)
            else:
                vfm.setFlag(plViewFaceModifier.kPivotFace, True)

            if self.offset:
                vfm.offset = hsVector3(*self.offset_coord)
                if self.offset_local:
                    vfm.setFlag(plViewFaceModifier.kOffsetLocal, True)

    @property
    def requires_actor(self):
        return True


class PlasmaVisControl(PlasmaModifierProperties):
    pl_id = "visregion"

    bl_category = "Render"
    bl_label = "Visibility Control"
    bl_description = "Controls object visibility using VisRegions"

    mode = EnumProperty(name="Mode",
                        description="Purpose of the VisRegion",
                        items=[("normal", "Normal", "Objects are only visible when the camera is inside this region"),
                               ("exclude", "Exclude", "Objects are only visible when the camera is outside this region"),
                               ("fx", "Special FX", "This is a list of objects used for special effects only")])
    softvolume = StringProperty(name="Region",
                                description="Object defining the SoftVolume for this VisRegion")
    replace_normal = BoolProperty(name="Hide Drawables",
                                  description="Hides drawables attached to this region",
                                  default=True)

    def export(self, exporter, bo, so):
        rgn = exporter.mgr.find_create_object(plVisRegion, bl=bo, so=so)
        rgn.setProperty(plVisRegion.kReplaceNormal, self.replace_normal)

        if self.mode == "fx":
            rgn.setProperty(plVisRegion.kDisable, True)
        else:
            this_sv = bo.plasma_modifiers.softvolume
            if this_sv.enabled:
                print("    [VisRegion] I'm a SoftVolume myself :)")
                rgn.region = this_sv.get_key(exporter, so)
            else:
                print("    [VisRegion] SoftVolume '{}'".format(self.softvolume))
                sv_bo = bpy.data.objects.get(self.softvolume, None)
                if sv_bo is None:
                    raise ExportError("'{}': Invalid object '{}' for VisControl soft volume".format(bo.name, self.softvolume))
                sv = sv_bo.plasma_modifiers.softvolume
                if not sv.enabled:
                    raise ExportError("'{}': '{}' is not a SoftVolume".format(bo.name, self.softvolume))
                rgn.region = sv.get_key(exporter)
            rgn.setProperty(plVisRegion.kIsNot, self.mode == "exclude")


class VisRegion(bpy.types.PropertyGroup):
    enabled = BoolProperty(default=True)
    region_name = StringProperty(name="Control",
                                 description="Object defining a Plasma Visibility Control")


class PlasmaVisibilitySet(PlasmaModifierProperties):
    pl_id = "visibility"

    bl_category = "Render"
    bl_label = "Visibility Set"
    bl_description = "Defines areas where this object is visible"

    regions = CollectionProperty(name="Visibility Regions",
                                 type=VisRegion)
    active_region_index = IntProperty(options={"HIDDEN"})

    def export(self, exporter, bo, so):
        if not self.regions:
            # TODO: Log message about how this modifier is totally worthless
            return

        # Currently, this modifier is valid for meshes and lamps
        if bo.type == "MESH":
            diface = exporter.mgr.find_create_object(plDrawInterface, bl=bo, so=so)
            addRegion = diface.addRegion
        elif bo.type == "LAMP":
            light = exporter.light.get_light_key(bo, bo.data, so)
            addRegion = light.object.addVisRegion

        for region in self.regions:
            if not region.enabled:
                continue
            rgn_bo = bpy.data.objects.get(region.region_name, None)
            if rgn_bo is None:
                raise ExportError("{}: Invalid VisControl '{}' in VisSet modifier".format(bo.name, region.region_name))
            addRegion(exporter.mgr.find_create_key(plVisRegion, bl=rgn_bo))
