package net.minecraft.network.protocol.game;

public final class ClientboundPlayerInfoUpdatePacket {
    public static final class Entry {
        private final net.minecraft.network.chat.RemoteChatSession.Data chatSession;

        public Entry(net.minecraft.network.chat.RemoteChatSession.Data chatSession) {
            this.chatSession = chatSession;
        }

        public net.minecraft.network.chat.RemoteChatSession.Data chatSession() {
            chatfixture.Counts.sessions++;
            return chatSession;
        }
    }
}
