package net.minecraft.util;

public interface SignatureValidator {
    boolean validate(byte[] payload, byte[] signature);
}
