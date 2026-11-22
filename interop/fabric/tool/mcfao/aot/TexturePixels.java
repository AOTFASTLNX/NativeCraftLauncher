package mcfao.aot;

import com.mojang.blaze3d.platform.NativeImage;

/** The selected client's pixel API, returning the ImageIO-compatible ARGB layout. */
final class TexturePixels {
    static int argb(NativeImage image, int x, int y) { return image.getPixel(x, y); }
}
