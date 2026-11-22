package net.minecraft.client;

public final class Minecraft {
    public static boolean keysAvailable;
    public static boolean validatorPresent;
    public static boolean blockKeys;
    public static boolean validatorThrows;

    public boolean canValidateProfileKeys() {
        chatfixture.Counts.keys++;
        if (blockKeys) chatfixture.Counts.awaitKeys();
        return keysAvailable;
    }

    public net.minecraft.util.SignatureValidator getProfileKeySignatureValidator() {
        chatfixture.Counts.validators++;
        if (validatorThrows) throw new AssertionError("profile-key validator must not be fetched");
        return validatorPresent ? new chatfixture.Validator() : null;
    }
}
