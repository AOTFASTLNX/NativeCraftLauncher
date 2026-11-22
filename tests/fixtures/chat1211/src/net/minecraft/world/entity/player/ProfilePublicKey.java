package net.minecraft.world.entity.player;

public final class ProfilePublicKey {
    public static final class Data {}

    public static final class ValidationException extends Exception {
        public ValidationException(String message) { super(message); }
    }
}
