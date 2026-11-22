package net.minecraft.client.multiplayer;

import com.mojang.authlib.GameProfile;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.RemoteChatSession;
import net.minecraft.network.protocol.game.ClientboundPlayerInfoUpdatePacket;
import net.minecraft.util.SignatureValidator;
import net.minecraft.world.entity.player.ProfilePublicKey;

/**
 * Same-name 1.21.1 fixture. The two methods keep vanilla order: the predicate is
 * {@code keys && flag}, and the initializer fetches the validator before chatSession().
 */
public class ClientPacketListener {
    private final Minecraft minecraft;
    private boolean serverEnforcesSecureChat;

    public ClientPacketListener(Minecraft minecraft) { this.minecraft = minecraft; }

    public void setServerEnforcesSecureChat(boolean value) { this.serverEnforcesSecureChat = value; }

    private void initializeChatSession(ClientboundPlayerInfoUpdatePacket.Entry entry, PlayerInfo player) {
        GameProfile profile = player.getProfile();
        SignatureValidator validator = this.minecraft.getProfileKeySignatureValidator();
        if (validator == null) {
            player.clearChatSession(this.enforcesSecureChat());
            return;
        }
        RemoteChatSession.Data session = entry.chatSession();
        if (session != null) {
            try {
                RemoteChatSession validated = session.validate(profile, validator);
                player.setChatSession(validated);
            } catch (ProfilePublicKey.ValidationException error) {
                chatfixture.Counts.validationErrors++;
                player.clearChatSession(this.enforcesSecureChat());
            }
        } else {
            player.clearChatSession(this.enforcesSecureChat());
        }
    }

    private boolean enforcesSecureChat() {
        return this.minecraft.canValidateProfileKeys() && this.serverEnforcesSecureChat;
    }
}
