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
import math
from pathlib import Path
from PyHSPlasma import *
import weakref

from . import explosions
from .. import helpers
from .. import korlib
from . import utils

class _Texture:
    def __init__(self, texture=None, image=None, use_alpha=None, force_calc_alpha=False):
        assert (texture or image)

        if texture is not None:
            if image is None:
                image = texture.image
            self.calc_alpha = texture.use_calculate_alpha
            self.mipmap = texture.use_mipmap
        else:
            self.calc_alpha = False
            self.mipmap = False

        if force_calc_alpha or self.calc_alpha:
            self.calc_alpha = True
            self.use_alpha  = True
        elif use_alpha is None:
            self.use_alpha = (image.channels == 4 and image.use_alpha)
        else:
            self.use_alpha = use_alpha

        self.image = image

    def __eq__(self, other):
        if not isinstance(other, _Texture):
            return False

        if self.image == other.image:
            if self.calc_alpha == other.calc_alpha:
                self._update(other)
                return True

    def __hash__(self):
        return hash(self.image.name) ^ hash(self.calc_alpha)

    def __str__(self):
        if self.mipmap:
            name = str(Path(self.image.name).with_suffix(".dds"))
        else:
            name = str(Path(self.image.name).with_suffix(".bmp"))
        if self.calc_alpha:
            name = "ALPHAGEN_{}".format(name)
        return name

    def _update(self, other):
        """Update myself with any props that might be overridable from another copy of myself"""
        if other.use_alpha:
            self.use_alpha = True
        if other.mipmap:
            self.mipmap = True


class MaterialConverter:
    def __init__(self, exporter):
        self._obj2mat = {}
        self._exporter = weakref.ref(exporter)
        self._pending = {}
        self._alphatest = {}
        self._tex_exporters = {
            "ENVIRONMENT_MAP": self._export_texture_type_environment_map,
            "IMAGE": self._export_texture_type_image,
            "NONE": self._export_texture_type_none,
        }
        self._animation_exporters = {
            "opacityCtl": self._export_layer_opacity_animation,
            "transformCtl": self._export_layer_transform_animation,
        }

    def export_material(self, bo, bm):
        """Exports a Blender Material as an hsGMaterial"""
        print("    Exporting Material '{}'".format(bm.name))

        hsgmat = self._mgr.add_object(hsGMaterial, name=bm.name, bl=bo)
        slots = [slot for slot in bm.texture_slots if slot is not None and slot.use and
                 slot.texture is not None and slot.texture.type in self._tex_exporters]

        # Okay, I know this isn't Pythonic... But we're doing it this way because we might actually
        # export many slots in one go. Think stencils.
        i = 0
        while i < len(slots):
            i += self._export_texture_slot(bo, bm, hsgmat, slots, i)

        # Plasma makes several assumptions that every hsGMaterial has at least one layer. If this
        # material had no Textures, we will need to initialize a default layer
        if not hsgmat.layers:
            layer = self._mgr.add_object(plLayer, name="{}_AutoLayer".format(bm.name), bl=bo)
            self._propagate_material_settings(bm, layer)
            hsgmat.addLayer(layer.key)

        # Cache this material for later
        if bo in self._obj2mat:
            self._obj2mat[bo].append(hsgmat.key)
        else:
            self._obj2mat[bo] = [hsgmat.key]

        # Looks like we're done...
        return hsgmat.key

    def export_waveset_material(self, bo, bm):
        print("    Exporting WaveSet Material '{}'".format(bm.name))

        # WaveSets MUST have their own material
        unique_name = "{}_WaveSet7".format(bm.name)
        hsgmat = self._mgr.add_object(hsGMaterial, name=unique_name, bl=bo)

        # Materials MUST have one layer. Wavesets need alpha blending...
        layer = self._mgr.add_object(plLayer, name=unique_name, bl=bo)
        self._propagate_material_settings(bm, layer)
        layer.state.blendFlags |= hsGMatState.kBlendAlpha
        hsgmat.addLayer(layer.key)

        # Wasn't that easy?
        return hsgmat.key

    def _export_texture_slot(self, bo, bm, hsgmat, slots, idx):
        slot = slots[idx]
        num_exported = 1

        name = "{}_{}".format(bm.name, slot.name)
        print("        Exporting Plasma Layer '{}'".format(name))
        layer = self._mgr.add_object(plLayer, name=name, bl=bo)
        self._propagate_material_settings(bm, layer)

        # UVW Channel
        for i, uvchan in enumerate(bo.data.uv_layers):
            if uvchan.name == slot.uv_layer:
                layer.UVWSrc = i
                print("            Using UV Map #{} '{}'".format(i, name))
                break
        else:
            print("            No UVMap specified... Blindly using the first one, maybe it exists :|")

        # Transform
        xform = hsMatrix44()
        xform.setTranslate(hsVector3(*slot.offset))
        xform.setScale(hsVector3(*slot.scale))
        layer.transform = xform

        state = layer.state
        if slot.use_stencil:
            hsgmat.compFlags |= hsGMaterial.kCompNeedsBlendChannel
            state.blendFlags |= hsGMatState.kBlendAlpha | hsGMatState.kBlendAlphaMult | hsGMatState.kBlendNoTexColor
            if slot.texture.type == "BLEND":
                state.clampFlags |= hsGMatState.kClampTexture
            state.ZFlags |= hsGMatState.kZNoZWrite
            layer.ambient = hsColorRGBA(1.0, 1.0, 1.0, 1.0)

            # Plasma actually wants the next layer first, so let's export him
            nextIdx = idx + 1
            if len(slots) == nextIdx:
                raise ExportError("Texture Slot '{}' wants to be a stencil, but there are no more TextureSlots.".format(slot.name))
            print("            --- BEGIN STENCIL ---")
            self._export_texture_slot(bo, bm, hsgmat, slots, nextIdx)
            print("            ---  END STENCIL  ---")
            num_exported += 1

            # Now that we've exported the bugger, flag him as binding with this texture
            prev_layer = hsgmat.layers[-1].object
            prev_state = prev_layer.state
            prev_state.miscFlags |= hsGMatState.kMiscBindNext | hsGMatState.kMiscRestartPassHere
            if not prev_state.blendFlags & hsGMatState.kBlendMask:
                prev_state.blendFlags |= hsGMatState.kBlendAlpha
        else:
            # Standard layer flags ahoy
            if slot.blend_type == "ADD":
                state.blendFlags |= hsGMatState.kBlendAddColorTimesAlpha
            elif slot.blend_type == "MULTIPLY":
                state.blendFlags |= hsGMatState.kBlendMult

        texture = slot.texture

        # Apply custom layer properties
        layer_props = texture.plasma_layer
        layer.opacity = layer_props.opacity / 100
        if layer_props.opacity < 100:
            state.blendFlags |= hsGMatState.kBlendAlpha
        if layer_props.alpha_halo:
            state.blendFlags |= hsGMatState.kBlendAlphaTestHigh

        # Export the specific texture type
        self._tex_exporters[texture.type](bo, hsgmat, layer, slot)

        # Export any layer animations
        layer = self._export_layer_animations(bo, bm, slot, idx, hsgmat, layer)

        hsgmat.addLayer(layer.key)
        return num_exported

    def _export_layer_animations(self, bo, bm, tex_slot, idx, hsgmat, base_layer):
        """Exports animations on this texture and chains the Plasma layers as needed"""

        def harvest_fcurves(bl_id, collection, data_path=None):
            anim = bl_id.animation_data
            if anim is not None:
                action = anim.action
                if action is not None:
                    if data_path is None:
                        collection.extend(action.fcurves)
                    else:
                        collection.extend([i for i in action.fcurves if i.data_path.startswith(data_path)])
                    return action
            return None

        # First, we must gather relevant FCurves from both the material and the texture itself
        # Because, you know, that totally makes sense...
        fcurves = []
        mat_action = harvest_fcurves(bm, fcurves, "texture_slots[{}]".format(idx))
        tex_action = harvest_fcurves(tex_slot.texture, fcurves)

        # No fcurves, no animation
        if not fcurves:
            return base_layer

        # Okay, so we have some FCurves. We'll loop through our known layer animation converters
        # and chain this biotch up as best we can.
        layer_animation = None
        for attr, converter in self._animation_exporters.items():
            ctrl = converter(bm, tex_slot, base_layer, fcurves)
            if ctrl is not None:
                if layer_animation is None:
                    name = "{}_LayerAnim".format(base_layer.key.name)
                    layer_animation = self._mgr.add_object(plLayerAnimation, bl=bo, name=name)
                setattr(layer_animation, attr, ctrl)

        # Alrighty, if we exported any controllers, layer_animation is a plLayerAnimation. We need to do
        # the common schtuff now.
        if layer_animation is not None:
            layer_animation.underLay = base_layer.key

            fps = bpy.context.scene.render.fps
            atc = layer_animation.timeConvert
            if tex_action is not None:
                start, end = tex_action.frame_range
            else:
                start, end = mat_action.frame_range
            atc.begin = start / fps
            atc.end = end / fps

            layer_props = tex_slot.texture.plasma_layer
            if not layer_props.anim_auto_start:
                atc.flags |= plAnimTimeConvert.kStopped
            if layer_props.anim_loop:
                atc.flags |= plAnimTimeConvert.kLoop
                atc.loopBegin = atc.begin
                atc.loopEnd = atc.end
            return layer_animation

        # Well, we had some FCurves but they were garbage... Too bad.
        return base_layer

    def _export_layer_opacity_animation(self, bm, tex_slot, base_layer, fcurves):
        for i in fcurves:
            if i.data_path == "plasma_layer.opacity":
                base_layer.state.blendFlags |= hsGMatState.kBlendAlpha
                ctrl = self._exporter().animation.make_scalar_leaf_controller(i)
                return ctrl
        return None

    def _export_layer_transform_animation(self, bm, tex_slot, base_layer, fcurves):
        pos_fcurves = [i for i in fcurves if i.data_path.find("offset") != -1]
        scale_fcurves = [i for i in fcurves if i.data_path.find("scale") != -1]

        # Plasma uses the controller to generate a matrix44... so we have to produce a leaf controller
        ctrl = self._exporter().animation.make_matrix44_controller(pos_fcurves, scale_fcurves, tex_slot.offset, tex_slot.scale)
        return ctrl

    def _export_texture_type_environment_map(self, bo, hsgmat, layer, slot):
        """Exports a Blender EnvironmentMapTexture to a plLayer"""

        texture = slot.texture
        bl_env = texture.environment_map
        if bl_env.source in {"STATIC", "ANIMATED"}:
            if bl_env.mapping == "PLANE" and self._mgr.getVer() >= pvMoul:
                pl_env = plDynamicCamMap
            else:
                pl_env = plDynamicEnvMap
            pl_env = self.export_dynamic_env(bo, hsgmat, layer, texture, pl_env)
        else:
            # We should really export a CubicEnvMap here, but we have a good setup for DynamicEnvMaps
            # that create themselves when the explorer links in, so really... who cares about CEMs?
            self._exporter().report.warn("IMAGE EnvironmentMaps are not supported. '{}' will not be exported!".format(layer.key.name))
            pl_env = None
        layer.state.shadeFlags |= hsGMatState.kShadeEnvironMap
        layer.texture = pl_env.key

    def export_dynamic_env(self, bo, hsgmat, layer, texture, pl_class):
        # To protect the user from themselves, let's check to make sure that a DEM/DCM matching this
        # viewpoint object has not already been exported...
        bl_env = texture.environment_map
        viewpt = bl_env.viewpoint_object
        if viewpt is None:
            viewpt = bo
        name = "{}_DynEnvMap".format(viewpt.name)
        pl_env = self._mgr.find_object(pl_class, bl=bo, name=name)
        if pl_env is not None:
            print("            EnvMap for viewpoint {} already exported... NOTE: Your settings here will be overridden by the previous object!".format(viewpt.name))
            if isinstance(pl_env, plDynamicCamMap):
                pl_env.addTargetNode(self._mgr.find_key(plSceneObject, bl=bo))
                pl_env.addMatLayer(layer.key)
            return pl_env

        # Ensure POT
        oRes = bl_env.resolution
        eRes = helpers.ensure_power_of_two(oRes)
        if oRes != eRes:
            print("            Overriding EnvMap size to ({}x{}) -- POT".format(eRes, eRes))

        # And now for the general ho'hum-ness
        pl_env = self._mgr.add_object(pl_class, bl=bo, name=name)
        pl_env.hither = bl_env.clip_start
        pl_env.yon = bl_env.clip_end
        pl_env.refreshRate = 0.01 if bl_env.source == "ANIMATED" else 0.0
        pl_env.incCharacters = True

        # Perhaps the DEM/DCM fog should be separately configurable at some point?
        pl_fog = bpy.context.scene.world.plasma_fni
        pl_env.color = utils.color(texture.plasma_layer.envmap_color)
        pl_env.fogStart = pl_fog.fog_start

        # EffVisSets
        # Whoever wrote this PyHSPlasma binding didn't follow the convention. Sigh.
        visregions = []
        for region in texture.plasma_layer.vis_regions:
            rgn = bpy.data.objects.get(region.region_name, None)
            if rgn is None:
                raise ExportError("'{}': VisControl '{}' not found".format(texture.name, region.region_name))
            if not rgn.plasma_modifiers.visregion.enabled:
                raise ExportError("'{}': '{}' is not a VisControl".format(texture.name, region.region_name))
            visregions.append(self._mgr.find_create_key(plVisRegion, bl=rgn))
        pl_env.visRegions = visregions

        if isinstance(pl_env, plDynamicCamMap):
            faces = (pl_env,)

            # It matters not whether or not the viewpoint object is a Plasma Object, it is exported as at
            # least a SceneObject and CoordInterface so that we can touch it...
            # NOTE: that harvest_actor makes sure everyone alread knows we're going to have a CI
            root = self._mgr.find_create_key(plSceneObject, bl=viewpt)
            pl_env.rootNode = root # FIXME: DCM camera
            # FIXME: DynamicCamMap Camera

            pl_env.addTargetNode(self._mgr.find_key(plSceneObject, bl=bo))
            pl_env.addMatLayer(layer.key)

            # This is really just so we don't raise any eyebrows if anyone is looking at the files.
            # If you're disabling DCMs, then you're obviuously trolling!
            # Cyan generates a single color image, but we'll just set the layer colors and go away.
            fake_layer = self._mgr.add_object(plLayer, bl=bo, name="{}_DisabledDynEnvMap".format(viewpt.name))
            fake_layer.ambient = layer.ambient
            fake_layer.preshade = layer.preshade
            fake_layer.runtime = layer.runtime
            fake_layer.specular = layer.specular
            pl_env.disableTexture = fake_layer.key

            if pl_env.camera is None:
                layer.UVWSrc = plLayerInterface.kUVWPosition
                layer.state.miscFlags |= (hsGMatState.kMiscCam2Screen | hsGMatState.kMiscPerspProjection)
        else:
            faces = pl_env.faces + (pl_env,)

            # DEMs can do just a position vector. We actually prefer this because the WaveSet exporter
            # will probably want to steal it for diabolical purposes...
            pl_env.position = hsVector3(*viewpt.location)

            if layer is not None:
                layer.UVWSrc = plLayerInterface.kUVWReflect
                layer.state.miscFlags |= hsGMatState.kMiscUseRefractionXform

        # Because we might be working with a multi-faced env map. It's even worse than have two faces...
        for i in faces:
            i.setConfig(plBitmap.kRGB8888)
            i.flags |= plBitmap.kIsTexture
            i.flags &= ~plBitmap.kAlphaChannelFlag
            i.width = eRes
            i.height = eRes
            i.proportionalViewport = False
            i.viewportLeft = 0
            i.viewportTop = 0
            i.viewportRight = eRes
            i.viewportBottom = eRes
            i.ZDepth = 24

        return pl_env

    def _export_texture_type_image(self, bo, hsgmat, layer, slot):
        """Exports a Blender ImageTexture to a plLayer"""
        texture = slot.texture

        # Does the image have any alpha at all?
        if texture.image is not None:
            has_alpha = texture.use_calculate_alpha or slot.use_stencil or self._test_image_alpha(texture.image)
            if (texture.image.use_alpha and texture.use_alpha) and not has_alpha:
                warning = "'{}' wants to use alpha, but '{}' is opaque".format(texture.name, texture.image.name)
                self._exporter().report.warn(warning, indent=3)
        else:
            has_alpha = True

        # First, let's apply any relevant flags
        state = layer.state
        if not slot.use_stencil:
            # mutually exclusive blend flags
            if texture.use_alpha and has_alpha:
                if slot.blend_type == "ADD":
                    state.blendFlags |= hsGMatState.kBlendAlphaAdd
                elif slot.blend_type == "MULTIPLY":
                    state.blendFlags |= hsGMatState.kBlendAlphaMult
                else:
                    state.blendFlags |= hsGMatState.kBlendAlpha

            if texture.invert_alpha and has_alpha:
                state.blendFlags |= hsGMatState.kBlendInvertAlpha
        if texture.extension == "CLIP":
            state.clampFlags |= hsGMatState.kClampTexture

        # Now, let's export the plBitmap
        # If the image is None (no image applied in Blender), we assume this is a plDynamicTextMap
        # Otherwise, we toss this layer and some info into our pending texture dict and process it
        #     when the exporter tells us to finalize all our shit
        if texture.image is None:
            dtm = self._mgr.find_create_object(plDynamicTextMap, name="{}_DynText".format(layer.key.name), bl=bo)
            dtm.hasAlpha = texture.use_alpha
            # if you have a better idea, let's hear it...
            dtm.visWidth, dtm.visHeight = 1024, 1024
            layer.texture = dtm.key
        else:
            key = _Texture(texture=texture, use_alpha=has_alpha, force_calc_alpha=slot.use_stencil)
            if key not in self._pending:
                print("            Stashing '{}' for conversion as '{}'".format(texture.image.name, str(key)))
                self._pending[key] = [layer.key,]
            else:
                print("            Found another user of '{}'".format(texture.image.name))
                self._pending[key].append(layer.key)

    def _export_texture_type_none(self, bo, hsgmat, layer, texture):
        # We'll allow this, just for sanity's sake...
        pass

    def export_prepared_layer(self, layer, image):
        """This exports an externally prepared layer and image"""
        key = _Texture(image=image)
        if key not in self._pending:
            print("        Stashing '{}' for conversion as '{}'".format(image.name, str(key)))
            self._pending[key] = [layer.key,]
        else:
            print("        Found another user of '{}'".format(image.name))
            self._pending[key].append(layer.key)

    def finalize(self):
        for key, layers in self._pending.items():
            name = str(key)
            print("\n[Mipmap '{}']".format(name))

            image = key.image
            oWidth, oHeight = image.size
            eWidth = helpers.ensure_power_of_two(oWidth)
            eHeight = helpers.ensure_power_of_two(oHeight)
            if (eWidth != oWidth) or (eHeight != oHeight):
                print("    Image is not a POT ({}x{}) resizing to {}x{}".format(oWidth, oHeight, eWidth, eHeight))
                self._resize_image(image, eWidth, eHeight)

            # Some basic mipmap settings.
            numLevels = math.floor(math.log(max(eWidth, eHeight), 2)) + 1 if key.mipmap else 1
            compression = plBitmap.kDirectXCompression if key.mipmap else plBitmap.kUncompressed
            dxt = plBitmap.kDXT5 if key.use_alpha or key.calc_alpha else plBitmap.kDXT1

            # Major Workaround Ahoy
            # There is a bug in Cyan's level size algorithm that causes it to not allocate enough memory
            # for the color block in certain mipmaps. I personally have encountered an access violation on
            # 1x1 DXT5 mip levels -- the code only allocates an alpha block and not a color block. Paradox
            # reports that if any dimension is smaller than 4px in a mip level, OpenGL doesn't like Cyan generated
            # data. So, we're going to lop off the last two mip levels, which should be 1px and 2px as the smallest.
            # This bug is basically unfixable without crazy hacks because of the way Plasma reads in texture data.
            #     "<Deledrius> I feel like any texture at a 1x1 level is essentially academic.  I mean, JPEG/DXT
            #                  doesn't even compress that, and what is it?  Just the average color of the whole
            #                  texture in a single pixel?"
            # :)
            if key.mipmap:
                # If your mipmap only has 2 levels (or less), then you deserve to phail...
                numLevels = max(numLevels - 2, 2)

            # Grab the image data from OpenGL and stuff it into the plBitmap
            helper = korlib.GLTexture(image)
            with helper as glimage:
                if key.mipmap:
                    print("    Generating mip levels")
                    glimage.generate_mipmap()
                else:
                    print("    Stuffing image data")

                # Uncompressed bitmaps are BGRA
                fmt = compression == plBitmap.kUncompressed

                # Hold the uncompressed level data for now. We may have to make multiple copies of
                # this mipmap for per-page textures :(
                data = []
                for i in range(numLevels):
                    data.append(glimage.get_level_data(i, key.calc_alpha, fmt))

            # Be a good citizen and reset the Blender Image to pre-futzing state
            image.reload()

            # Now we poke our new bitmap into the pending layers. Note that we have to do some funny
            # business to account for per-page textures
            mgr = self._mgr
            pages = {}

            print("    Adding to Layer(s)")
            for layer in layers:
                print("        {}".format(layer.name))
                page = mgr.get_textures_page(layer) # Layer's page or Textures.prp

                # If we haven't created this plMipmap in the page (either layer's page or Textures.prp),
                # then we need to do that and stuff the level data. This is a little tedious, but we
                # need to be careful to manage our resources correctly
                if page not in pages:
                    mipmap = plMipmap(name=name, width=eWidth, height=eHeight, numLevels=numLevels,
                                      compType=compression, format=plBitmap.kRGB8888, dxtLevel=dxt)
                    helper.store_in_mipmap(mipmap, data, compression)
                    mgr.AddObject(page, mipmap)
                    pages[page] = mipmap
                else:
                    mipmap = pages[page]
                layer.object.texture = mipmap.key

    def get_materials(self, bo):
        return self._obj2mat[bo]

    @property
    def _mgr(self):
        return self._exporter().mgr

    def _propagate_material_settings(self, bm, layer):
        """Converts settings from the Blender Material to corresponding plLayer settings"""
        state = layer.state

        # Shade Flags
        if not bm.use_mist:
            state.shadeFlags |= hsGMatState.kShadeNoFog # Dead in CWE
            state.shadeFlags |= hsGMatState.kShadeReallyNoFog

        # Colors
        layer.ambient = utils.color(bpy.context.scene.world.ambient_color)
        layer.preshade = utils.color(bm.diffuse_color)
        layer.runtime = utils.color(bm.diffuse_color)
        layer.specular = utils.color(bm.specular_color)

    def _resize_image(self, image, width, height):
        image.scale(width, height)

        # If the image is already loaded into OpenGL, we need to refresh it to get the scaling.
        if image.bindcode != 0:
            image.gl_free()
            image.gl_load()

    def _test_image_alpha(self, image):
        """Tests to see if this image has any alpha data"""

        # In the interest of speed, let's see if we've already done this one...
        result = self._alphatest.get(image, None)
        if result is not None:
            return result

        if image.channels != 4:
            result = False
        elif not image.use_alpha:
            result = False
        else:
            # Using bpy.types.Image.pixels is VERY VERY VERY slow...
            with korlib.GLTexture(image) as glimage:
                result = glimage.has_alpha

        self._alphatest[image] = result
        return result
