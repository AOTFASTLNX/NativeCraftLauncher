package chatfixture;

import net.minecraft.util.SignatureValidator;

public final class Validator implements SignatureValidator {
    @Override
    public boolean validate(byte[] payload, byte[] signature) {
        Counts.validatorCalls++;
        return true;
    }
}
