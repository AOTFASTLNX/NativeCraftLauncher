package net.minecraft.client.multiplayer;

import com.mojang.authlib.GameProfile;
import net.minecraft.network.chat.RemoteChatSession;

public class PlayerInfo {
    private final GameProfile profile;
    public RemoteChatSession chatSession;
    public boolean cleared;
    public boolean clearEnforced;

    public PlayerInfo(GameProfile profile) { this.profile = profile; }

    public GameProfile getProfile() { return profile; }

    protected void setChatSession(RemoteChatSession session) {
        this.chatSession = session;
        chatfixture.Counts.setCalls++;
    }

    protected void clearChatSession(boolean enforced) {
        this.chatSession = null;
        this.cleared = true;
        this.clearEnforced = enforced;
        chatfixture.Counts.clearCalls++;
        chatfixture.Counts.lastEnforced = enforced;
    }
}
