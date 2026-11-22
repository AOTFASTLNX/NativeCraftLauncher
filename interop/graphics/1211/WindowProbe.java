package fabricaot.graphics;
import java.util.OptionalInt;
import java.util.Optional;
import com.mojang.blaze3d.platform.*;
import com.mojang.blaze3d.systems.RenderSystem;
import static org.lwjgl.glfw.GLFW.*;

/**
 * Executes the real Minecraft 1.21.1 startup path around Window.
 *
 * 1.21.1 has no GpuBackend indirection: Window itself calls glfwMakeContextCurrent and
 * GL.createCapabilities() while forcing GLFW_NO_API, so construction needs a real current
 * GL context. The host fixture has no Mesa context; this probe verifies every step before
 * that point and reports the missing context as an explicit blocked scope.
 */
public final class WindowProbe {
    private static void check(boolean value,String reason){if(!value)throw new AssertionError(reason);}
    public static void main(String[] args)throws Exception{
        RenderSystem.initRenderThread();
        RenderSystem.assertOnRenderThread();
        long timer=GLX._initGlfw().getAsLong();
        check(timer>0,"GLFW timer value");
        check(glfwGetPlatform()!=0,"GLFW platform");
        OptionalInt modeWidth=OptionalInt.of(390),modeHeight=OptionalInt.of(844);
        VideoMode parsed=VideoMode.read("1920x1080@60:24").orElseThrow(()->new AssertionError("VideoMode.read rejected a canonical mode"));
        check(parsed.getWidth()==1920&&parsed.getHeight()==1080&&parsed.getRefreshRate()==60,"VideoMode dimensions/refresh");
        check(parsed.getRedBits()==8&&parsed.getGreenBits()==8&&parsed.getBlueBits()==8,"VideoMode channel depth");
        check(VideoMode.read(parsed.write()).map(parsed::equals).orElse(false),"VideoMode write/read round trip");
        check(VideoMode.read("not-a-mode").isEmpty(),"VideoMode accepted malformed text");
        ScreenManager screens=new ScreenManager(Monitor::new);
        long primary=glfwGetPrimaryMonitor();
        check(primary!=0,"No primary GLFW monitor");
        Monitor monitor=screens.getMonitor(primary);
        check(monitor.getModeCount()>=1,"Monitor exposed no video modes");
        check(monitor.getPreferredVidMode(Optional.empty()).getWidth()>0,"Monitor preferred mode");
        System.out.println("MINECRAFT_WINDOW_PREFLIGHT version=1.21.1 videoModes="+monitor.getModeCount()
            +" platform="+Window.getPlatform()+" lwjgl="+GLX._getLWJGLVersion()+" timer="+timer);
        DisplayData display=new DisplayData(390,844,modeWidth,modeHeight,false);
        WindowEventHandler handler=new WindowEventHandler(){
            public void setWindowActive(boolean active){}
            public void resizeDisplay(){}
            public void cursorEntered(){}
        };
        Window window=null;
        Throwable failure=null;
        try{
            window=new Window(handler,screens,display,null,"1.21.1 WindowProbe");
        }catch(Throwable error){
            failure=error;
        }
        if(window!=null){
            try{
                check(window.getWindow()!=0,"Window handle");
                check(window.getWidth()>0&&window.getHeight()>0,"Window dimensions");
                check(window.getScreenWidth()>0&&window.getScreenHeight()>0,"Window framebuffer dimensions");
                glfwPollEvents();
                System.out.println("MINECRAFT_WINDOW_CONSTRUCTED version=1.21.1 platform="+Window.getPlatform()
                    +" framebuffer="+window.getScreenWidth()+"x"+window.getScreenHeight());
                window.close();
            }finally{
                if(glfwGetCurrentContext()!=0)glfwDestroyWindow(glfwGetCurrentContext());
            }
            System.out.println("MINECRAFT_WINDOW_PROBE_PASS version=1.21.1 renderer=NOT_INITIALIZED surface="+glfwGetVersionString());
            return;
        }
        /* The constructor leaked a GLFW window before failing; destroy it so the fixture stays clean. */
        long leaked=glfwGetCurrentContext();
        if(leaked!=0)glfwDestroyWindow(leaked);
        check(glfwGetCurrentContext()==0,"Failed Window constructor left a live GLFW window");
        String stage=classify(failure);
        System.out.println("MINECRAFT_WINDOW_BLOCKED version=1.21.1 stage="+stage
            +" windowCreated="+(leaked!=0)+" reason="+failure);
        throw new AssertionError("1.21.1 Window requires a current GL context; host fixture has no Mesa context"
            +" (stage="+stage+", exception="+failure+")",failure);
    }
    private static String classify(Throwable failure){
        for(Throwable cause=failure;cause!=null;cause=cause.getCause()){
            String text=cause.toString();
            if(cause instanceof UnsatisfiedLinkError&&text.contains("opengl"))return "GL_LIBRARY_UNAVAILABLE";
            if(text.contains("GLCapabilities")||text.contains("No OpenGL context")||text.contains("glGetString"))return "GL_CAPABILITIES_UNAVAILABLE";
            if(text.contains("GLFW-lite"))return "GLFW_STUB";
        }
        /* glfwMakeContextCurrent fails inside GLFW-lite, which reports through Window.bootCrash.
           Without lwjgl-tinyfd on the classpath that dialog itself throws NoClassDefFoundError,
           so the GL-context root cause only appears in the stderr error line. */
        for(Throwable cause=failure;cause!=null;cause=cause.getCause()){
            String text=String.valueOf(cause.getMessage())+cause;
            if(text.contains("tinyfd")||text.contains("TinyFileDialogs"))return "GL_CONTEXT_UNAVAILABLE_BOOT_DIALOG_MISSING";
        }
        return "UNEXPECTED";
    }
}
