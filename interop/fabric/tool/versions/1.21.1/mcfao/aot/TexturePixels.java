package mcfao.aot;

import com.mojang.blaze3d.platform.NativeImage;

/** 1.21.1 stores ABGR; texture samplers consume ImageIO's ARGB layout. */
final class TexturePixels {
    static int argb(NativeImage image, int x, int y) {
        int abgr = image.getPixelRGBA(x, y);
        return (abgr & 0xff00ff00) | ((abgr & 255) << 16) | ((abgr >>> 16) & 255);
    }
}
