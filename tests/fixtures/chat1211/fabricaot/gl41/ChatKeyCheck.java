package fabricaot.gl41;

import chatfixture.Counts;
import com.mojang.authlib.GameProfile;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.PlayerInfo;
import net.minecraft.network.chat.RemoteChatSession;
import net.minecraft.network.protocol.game.ClientboundPlayerInfoUpdatePacket;

/** Runs the vanilla fixture and the patched class side by side. */
public final class ChatKeyCheck {
    private static final String NAME = "net.minecraft.client.multiplayer.ClientPacketListener";

    private ChatKeyCheck() {}

    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    private static void environment(boolean keys, boolean block, boolean validator, boolean throwing) {
        Minecraft.keysAvailable = keys;
        Minecraft.blockKeys = block;
        Minecraft.validatorPresent = validator;
        Minecraft.validatorThrows = throwing;
    }

    private static Object listener(Class<?> type, boolean flag) throws Exception {
        Constructor<?> constructor = type.getConstructor(Minecraft.class);
        Object value = constructor.newInstance(new Minecraft());
        type.getMethod("setServerEnforcesSecureChat", boolean.class).invoke(value, flag);
        return value;
    }

    private static boolean enforce(Class<?> type, Object listener) throws Exception {
        Method method = type.getDeclaredMethod("enforcesSecureChat");
        method.setAccessible(true);
        return (Boolean) method.invoke(listener);
    }

    private static void initialize(Class<?> type, Object listener, RemoteChatSession.Data session,
                                   PlayerInfo info) throws Exception {
        Method method = type.getDeclaredMethod("initializeChatSession",
                ClientboundPlayerInfoUpdatePacket.Entry.class, PlayerInfo.class);
        method.setAccessible(true);
        method.invoke(listener, new ClientboundPlayerInfoUpdatePacket.Entry(session), info);
    }

    /** The vanilla predicate joins the services-key future; prove the fixture reproduces that wait. */
    private static void originalStillBlocks(Class<?> plain) throws Exception {
        Counts.reset();
        environment(false, true, true, false);
        Object listener = listener(plain, true);
        ExecutorService pool = Executors.newSingleThreadExecutor();
        try {
            Future<Boolean> pending = pool.submit(() -> enforce(plain, listener));
            Thread.sleep(400);
            check(!pending.isDone(), "vanilla predicate returned while the key future was unfinished");
            check(Counts.keys == 1, "vanilla predicate did not join the key future exactly once");
            Counts.openKeys();
            check(!pending.get(5, TimeUnit.SECONDS), "released vanilla predicate must return false");
        } finally {
            Counts.openKeys();
            pool.shutdownNow();
        }
    }

    private static void patchedFalseDoesNotWait(Class<?> patched) throws Exception {
        Counts.reset();
        environment(false, true, true, true);
        Object listener = listener(patched, false);
        ExecutorService pool = Executors.newSingleThreadExecutor();
        try {
            Future<Boolean> result = pool.submit(() -> enforce(patched, listener));
            check(!result.get(2, TimeUnit.SECONDS), "false serverEnforcesSecureChat must yield false");
            check(Counts.keys == 0, "false flag must not touch Minecraft.canValidateProfileKeys");
        } finally {
            Counts.openKeys();
            pool.shutdownNow();
        }
    }

    private static void patchedTrueKeepsCheck(Class<?> patched) throws Exception {
        Counts.reset();
        environment(true, false, true, false);
        Object listener = listener(patched, true);
        check(enforce(patched, listener), "keys available and enforcing must stay true");
        check(Counts.keys == 1, "true path must still consult Minecraft.canValidateProfileKeys once");

        Counts.reset();
        environment(false, false, true, false);
        check(!enforce(patched, listener), "missing keys must stay false");
        check(Counts.keys == 1, "missing keys must still consult Minecraft.canValidateProfileKeys once");

        // An enforcing server still waits for the key future; the guard only skips the
        // case where the flag already decides the result.
        Counts.reset();
        environment(true, true, true, false);
        ExecutorService pool = Executors.newSingleThreadExecutor();
        try {
            Future<Boolean> pending = pool.submit(() -> enforce(patched, listener));
            Thread.sleep(400);
            check(!pending.isDone(), "true path must still wait for an unfinished key future");
            check(Counts.keys == 1, "true path must join the key future exactly once");
            Counts.openKeys();
            check(pending.get(5, TimeUnit.SECONDS), "released key future must yield true");
        } finally {
            Counts.openKeys();
            pool.shutdownNow();
        }
    }

    private static void nullSessionSkipsValidator(Class<?> patched) throws Exception {
        Counts.reset();
        environment(false, false, false, true);
        Object listener = listener(patched, false);
        PlayerInfo info = new PlayerInfo(new GameProfile("fixture"));
        initialize(patched, listener, null, info);
        check(Counts.validators == 0, "null session must not fetch Minecraft.getProfileKeySignatureValidator");
        check(Counts.sessions == 1, "null session must be read exactly once");
        check(Counts.clearCalls == 1, "null session must clear the chat session");
        check(!info.clearEnforced, "false flag must clear without enforcement");
        check(info.chatSession == null, "cleared session must be null");

        Counts.reset();
        environment(true, false, false, true);
        listener = listener(patched, true);
        info = new PlayerInfo(new GameProfile("fixture"));
        initialize(patched, listener, null, info);
        check(Counts.validators == 0, "null session must not fetch the validator even when enforcing");
        check(Counts.keys == 1, "null session still derives enforcement from the predicate");
        check(info.clearEnforced, "enforcing flag must reach clearChatSession");
    }

    private static void presentSessionKeepsValidation(Class<?> patched) throws Exception {
        Counts.reset();
        environment(true, false, true, false);
        Object listener = listener(patched, true);
        PlayerInfo info = new PlayerInfo(new GameProfile("fixture"));
        initialize(patched, listener, new RemoteChatSession.Data("session"), info);
        check(Counts.validators == 1, "present session must still fetch the validator once");
        check(Counts.validations == 1, "present session must still be validated");
        check(Counts.validatorCalls == 1, "validator must still be invoked");
        check(Counts.setCalls == 1 && info.chatSession != null, "validated session must be installed");
        check(Counts.clearCalls == 0, "valid session must not be cleared");
        check(Counts.keys == 0, "successful validation keeps the vanilla path without the predicate");

        Counts.reset();
        environment(true, false, true, false);
        listener = listener(patched, true);
        info = new PlayerInfo(new GameProfile("fixture"));
        Counts.failValidation = true;
        initialize(patched, listener, new RemoteChatSession.Data("session"), info);
        check(Counts.validationErrors == 1, "validation failure must be caught, not propagated");
        check(Counts.clearCalls == 1, "validation failure must clear the session");
        check(Counts.keys == 1, "validation failure still derives enforcement");
        check(info.clearEnforced, "validation failure keeps the enforcing flag");

        Counts.reset();
        environment(true, false, false, false);
        listener = listener(patched, true);
        info = new PlayerInfo(new GameProfile("fixture"));
        initialize(patched, listener, new RemoteChatSession.Data("session"), info);
        check(Counts.validators == 1, "missing validator with session data must keep the vanilla fetch");
        // The guard reads the session first; the vanilla path then returns on the missing
        // validator before it reaches its own chatSession() call.
        check(Counts.sessions == 1, "missing validator must not re-read the session");
        check(Counts.clearCalls == 1 && Counts.setCalls == 0, "missing validator must clear the session");
    }

    public static void main(String[] args) throws Exception {
        // The listener and PlayerInfo share a package, so the patched class has to be on the
        // application class path: a child loader would turn vanilla package access into an error.
        Class<?> listener = Class.forName(NAME);
        if (args.length == 1 && args[0].equals("vanilla")) {
            originalStillBlocks(listener);
            System.out.println("CHAT_KEYS_1211_VANILLA_BLOCKS");
            return;
        }
        if (args.length != 1 || !args[0].equals("patched"))
            throw new IllegalArgumentException("usage: ChatKeyCheck vanilla|patched");
        patchedFalseDoesNotWait(listener);
        patchedTrueKeepsCheck(listener);
        nullSessionSkipsValidator(listener);
        presentSessionKeepsValidation(listener);
        System.out.println("CHAT_KEYS_1211_PASS");
    }
}
