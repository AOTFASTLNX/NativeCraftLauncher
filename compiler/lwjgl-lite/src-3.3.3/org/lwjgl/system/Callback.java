package org.lwjgl.system;

import org.lwjgl.system.libffi.FFICIF;

/** LWJGL 3.3.3 API backed exclusively by the signed static callback bank.
 * The CIF parameter preserves the upstream ABI; no libffi object is initialized. */
public abstract class Callback implements Pointer, NativeResource {
    private final long address;
    protected Callback(FFICIF ignored) { address = create(ignored, this); }
    protected Callback(long address) {
        if (address == 0) throw new IllegalArgumentException("Null callback address");
        this.address = address;
    }
    public long address() { return address; }
    public void free() { free(address()); }
    static long create(FFICIF ignored, Object instance) { return Upcalls.upcallCreate(instance); }
    public static <T extends CallbackI> T get(long address) { return Upcalls.upcallGet(address); }
    public static <T extends CallbackI> T getSafe(long address) { return address == 0 ? null : get(address); }
    public static void free(long address) { Upcalls.upcallFree(address); }
    public boolean equals(Object other) {
        return this == other || other instanceof Callback && address == ((Callback) other).address();
    }
    public int hashCode() { return Long.hashCode(address); }
    public String toString() { return String.format("%s pointer [0x%X]", getClass().getSimpleName(), address); }
}
