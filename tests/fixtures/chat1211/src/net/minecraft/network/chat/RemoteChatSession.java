package net.minecraft.network.chat;

import com.mojang.authlib.GameProfile;
import net.minecraft.util.SignatureValidator;
import net.minecraft.world.entity.player.ProfilePublicKey;

public final class RemoteChatSession {
    public static final class Data {
        private final String id;

        public Data(String id) { this.id = id; }

        public String id() { return id; }

        public RemoteChatSession validate(GameProfile profile, SignatureValidator validator)
                throws ProfilePublicKey.ValidationException {
            chatfixture.Counts.validations++;
            if (chatfixture.Counts.failValidation)
                throw new ProfilePublicKey.ValidationException("fixture signature rejected");
            if (!validator.validate(new byte[] {1}, new byte[] {2}))
                throw new ProfilePublicKey.ValidationException("fixture validator returned false");
            return new RemoteChatSession(this);
        }
    }

    private final Data data;

    public RemoteChatSession(Data data) { this.data = data; }

    public Data data() { return data; }
}
