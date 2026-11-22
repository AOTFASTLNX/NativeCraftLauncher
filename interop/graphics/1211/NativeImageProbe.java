package fabricaot.graphics;
import com.mojang.blaze3d.platform.NativeImage;
import java.nio.*;
import java.nio.file.*;
import java.util.*;
import org.lwjgl.stb.*;
import org.lwjgl.system.*;

/** First-party pixels through the real Minecraft 1.21.1 NativeImage and LWJGL 3.3.3 STB. */
public final class NativeImageProbe {
    private static void check(boolean value,String reason){if(!value)throw new AssertionError(reason);}
    public static void run(Path path)throws Exception {
        int[] colors={0xff123456,0x804488cc,0x00112233,0xffaabbcc,0xff102030,0xffcc4411};
        try(NativeImage image=new NativeImage(3,2,true)){
            for(int i=0;i<colors.length;i++)image.setPixelRGBA(i%3,i/3,colors[i]);
            image.writeToFile(path);
        }
        byte[] png=Files.readAllBytes(path);
        check(png.length>40,"PNG callback wrote no data");
        try(NativeImage decoded=NativeImage.read(png)){
            check(decoded.getWidth()==3&&decoded.getHeight()==2,"PNG dimensions");
            for(int i=0;i<colors.length;i++)check(decoded.getPixelRGBA(i%3,i/3)==colors[i],"PNG color/alpha mismatch "+i);
        }
        try(NativeImage source=new NativeImage(4,3,true);NativeImage scaled=new NativeImage(7,5,true)){
            source.fillRect(0,0,4,3,0xff336699);
            source.resizeSubRectTo(1,1,2,2,scaled);
            for(int y=0;y<5;y++)for(int x=0;x<7;x++)check(near(scaled.getPixelRGBA(x,y),0xff336699),"Resize stride/subrectangle "+x+","+y);
        }
        /* LWJGL 3.3.3 exposes these only as raw-pointer natives; NativeImage itself passes pointers. */
        ByteBuffer sourceBuffer=MemoryUtil.memAlloc(4*3*4);
        ByteBuffer scaledBuffer=MemoryUtil.memAlloc(7*5*4);
        try{
            for(int i=0;i<4*3;i++)sourceBuffer.putInt(i*4,0xff336699);
            long source=MemoryUtil.memAddress(sourceBuffer),scaled=MemoryUtil.memAddress(scaledBuffer);
            check(STBImageResize.nstbir_resize_uint8(source,4,3,0,scaled,7,5,0,4)!=0,"STB 3.3.3 rejected a valid subrectangle");
            for(int i=0;i<7*5;i++)check(near(scaledBuffer.getInt(i*4),0xff336699),"STB 3.3.3 resize pixels "+i);
            try{
                STBImageResize.nstbir_resize_uint8(source,0,3,0,scaled,7,5,0,4);
                throw new AssertionError("Invalid resize accepted");
            }catch(IllegalArgumentException expected){}
            /* A stride narrower than one row must fail closed instead of reading past the buffer. */
            try{
                STBImageResize.nstbir_resize_uint8(source,4,3,0,scaled,7,5,8,4);
                throw new AssertionError("Undersized output stride accepted");
            }catch(IllegalArgumentException expected){}
        }finally{MemoryUtil.memFree(scaledBuffer);MemoryUtil.memFree(sourceBuffer);}
        ByteBuffer pngSource=MemoryUtil.memAlloc(4*3*4);
        try{
            for(int i=0;i<4*3;i++)pngSource.putInt(i*4,0xff336699);
            try(STBIWriteCallback callback=new STBIWriteCallback(){public void invoke(long user,long data,int size){throw new IllegalStateException("callback fixture");}}){
                try{STBImageWrite.nstbi_write_png_to_func(callback.address(),0,4,3,4,MemoryUtil.memAddress(pngSource),0);throw new AssertionError("Callback exception swallowed");}
                catch(IllegalStateException expected){check(expected.getMessage().equals("callback fixture"),"Wrong callback exception");}
            }
        }finally{MemoryUtil.memFree(pngSource);}
        try(MemoryStack stack=MemoryStack.stackPush()){
            ByteBuffer invalid=stack.calloc(16);IntBuffer x=stack.mallocInt(1),y=stack.mallocInt(1),n=stack.mallocInt(1);
            check(STBImage.stbi_load_from_memory(invalid,x,y,n,4)==null,"Corrupt image accepted");
            check(STBImage.stbi_failure_reason()!=null,"Missing decode failure reason");
        }
        List<STBIWriteCallback> callbacks=new ArrayList<>();
        try{
            for(int i=0;i<32;i++)callbacks.add(new STBIWriteCallback(){public void invoke(long user,long data,int size){}});
            try(STBIWriteCallback excess=new STBIWriteCallback(){public void invoke(long user,long data,int size){}}){
                check(excess.address()!=0,"Excess callback allocated");
                throw new AssertionError("STB callback capacity ignored");
            }
            catch(IllegalStateException expected){check(expected.getMessage().contains("exhausted"),"Wrong callback capacity failure");}
        }finally{for(STBIWriteCallback callback:callbacks)callback.free();}
        String texturePixels=texturePixels(path.resolveSibling("native-image-texture-pixels.png"));
        System.out.println("MINECRAFT_NATIVE_IMAGE_PASS version=1.21.1 png=write/read RGBA/alpha=exact"
            +" resize=subrectangle/stb3.3.3 callback=static/exception/capacity corrupt=reject texturePixels="+texturePixels);
    }
    /**
     * Checks the real 1.21.1 mcfao.aot.TexturePixels.argb swizzle through a NativeImage PNG round
     * trip. The helper is package-private in mcfao.aot, so reflection is how this probe reaches it
     * without owning a second copy of the rule.
     *
     * 1.21.1 NativeImage stores ABGR, and getPixelRGBA returns that stored word unchanged: red sits
     * in bits 0..7. Texture samplers and ImageIO expect ARGB with red in 16..23. Every fixture
     * channel differs from its neighbours, so a red/blue swap cannot pass on a symmetric colour.
     */
    private static String texturePixels(Path path)throws Exception {
        Class<?> type;
        try{type=Class.forName("mcfao.aot.TexturePixels");}
        catch(ClassNotFoundException missing){
            throw new AssertionError("mcfao.aot.TexturePixels must be compiled with this probe:"
                +" interop/fabric/tool/versions/1.21.1/mcfao/aot/TexturePixels.java",missing);
        }
        java.lang.reflect.Method argb=type.getDeclaredMethod("argb",NativeImage.class,int.class,int.class);
        argb.setAccessible(true);
        int[] colors={0x80cc8844,0xff123456,0x00112233,0xffaabbcc,0xff102030,0x804488cc};
        try(NativeImage image=new NativeImage(3,2,true)){
            for(int i=0;i<colors.length;i++)image.setPixelRGBA(i%3,i/3,colors[i]);
            image.writeToFile(path);
        }
        int swizzled=0;
        try(NativeImage decoded=NativeImage.read(Files.readAllBytes(path))){
            check(decoded.getWidth()==3&&decoded.getHeight()==2,"TexturePixels PNG dimensions");
            for(int i=0;i<colors.length;i++){
                int x=i%3,y=i/3;
                int stored=decoded.getPixelRGBA(x,y);
                check(stored==colors[i],"TexturePixels PNG round trip changed pixel "+i);
                int expected=(stored&0xff00ff00)|((stored&0xff)<<16)|((stored>>>16)&0xff);
                int actual=(Integer)argb.invoke(null,decoded,x,y);
                check(actual==expected,"TexturePixels.argb "+x+","+y+": expected "
                    +Integer.toHexString(expected)+" got "+Integer.toHexString(actual));
                check((actual>>>24)==(stored>>>24),"TexturePixels.argb must preserve alpha at "+x+","+y);
                check((actual&0xff)==((stored>>>16)&0xff),"TexturePixels.argb must move blue to bits 0..7");
                check(((actual>>>16)&0xff)==(stored&0xff),"TexturePixels.argb must move red to bits 16..23");
                if(actual!=stored)swizzled++;
            }
            /* An unswizzled implementation (the 26.1.2 variant) returns the ABGR word unchanged. */
            check(swizzled==colors.length,"TexturePixels.argb returned ABGR unchanged for "
                +(colors.length-swizzled)+" of "+colors.length+" pixels; the 1.21.1 variant is not compiled");
            /* Stored 0x80cc8844 is ABGR, so the sampled word must be 0x804488cc. */
            check((Integer)argb.invoke(null,decoded,0,0)==0x804488cc,"TexturePixels.argb fixture ARGB");
        }
        return "ABGR_TO_ARGB_EXACT";
    }
    private static boolean near(int actual,int expected){
        for(int shift=0;shift<32;shift+=8){
            int a=(actual>>>shift)&0xff,e=(expected>>>shift)&0xff;
            if(Math.abs(a-e)>1)return false;
        }
        return true;
    }
    public static void main(String[] args)throws Exception {run(Path.of("native-image-fixture.png"));}
}
