package fabricaot.probe;

import io.netty.bootstrap.Bootstrap;
import io.netty.bootstrap.ServerBootstrap;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.Channel;
import io.netty.channel.ChannelFuture;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInboundHandlerAdapter;
import io.netty.channel.ChannelInitializer;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.local.LocalAddress;
import io.netty.channel.local.LocalChannel;
import io.netty.channel.local.LocalServerChannel;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.util.ReferenceCountUtil;
import io.netty.util.concurrent.Future;
import io.netty.util.internal.PlatformDependent;

import java.nio.charset.StandardCharsets;
import java.util.Queue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * First-party Netty 4.1.97 probe for the local-channel path.
 *
 * <p>It exercises only in-process Netty APIs: Unsafe-backed MPSC queues, a single-threaded
 * {@link NioEventLoopGroup}, and a LocalServerChannel/LocalChannel round trip through a
 * {@link LocalAddress}. No sockets, DNS, Minecraft classes, Mods, or third-party build scripts are
 * used. By default {@link PlatformDependent#hasUnsafe()} must be true so a non-Unsafe fallback
 * cannot hide the path being audited. Pass {@code --allow-no-unsafe} only for comparison runs.
 *
 * <p>Success prints {@code NETTY_LOCAL_CHANNEL_PASS} and exits 0; any failure prints
 * {@code NETTY_LOCAL_CHANNEL_FAIL}, a stack trace, and exits non-zero. The process always exits
 * explicitly after closing channels and the event loop group so it cannot hang.
 */
public final class NettyLocalChannelProbe {
    private static final String PAYLOAD = "netty-local-channel-probe-payload";
    private static final long TIMEOUT_SECONDS = 10L;
    private static final long SHUTDOWN_QUIET_PERIOD_SECONDS = 0L;
    private static final long SHUTDOWN_TIMEOUT_SECONDS = 5L;

    private NettyLocalChannelProbe() {}

    public static void main(String[] args) {
        boolean allowNoUnsafe = false;
        for (String arg : args) {
            if ("--allow-no-unsafe".equals(arg)) {
                allowNoUnsafe = true;
            } else {
                System.err.println("NETTY_LOCAL_CHANNEL_FAIL unknown argument: " + arg);
                System.exit(2);
            }
        }

        int exitCode;
        try {
            run(allowNoUnsafe);
            System.out.println("NETTY_LOCAL_CHANNEL_PASS");
            exitCode = 0;
        } catch (Throwable failure) {
            System.err.println("NETTY_LOCAL_CHANNEL_FAIL");
            failure.printStackTrace(System.err);
            exitCode = 1;
        }
        System.out.flush();
        System.err.flush();
        System.exit(exitCode);
    }

    private static void run(boolean allowNoUnsafe) throws Exception {
        System.out.println("NETTY_LOCAL_CHANNEL_BEGIN java.version=" + System.getProperty("java.version")
                + " java.vendor=" + System.getProperty("java.vendor"));

        boolean hasUnsafe = PlatformDependent.hasUnsafe();
        System.out.println("PlatformDependent.hasUnsafe=" + hasUnsafe
                + " PlatformDependent.isAndroid=" + PlatformDependent.isAndroid());
        if (!hasUnsafe && !allowNoUnsafe) {
            throw new AssertionError("PlatformDependent.hasUnsafe() is false; Netty would use a "
                    + "non-Unsafe fallback. Re-run with --allow-no-unsafe only for comparison.");
        }

        checkMpscQueue("unbounded", PlatformDependent.newMpscQueue(), 64);
        checkMpscQueue("bounded", PlatformDependent.newMpscQueue(16), 8);
        System.out.println("mpscQueue.checks=PASS");
        // Initialize before Bootstrap can replace the original allocator failure
        // with a secondary NoClassDefFoundError while creating FailedChannel.
        System.out.println("byteBufAllocator="
                + io.netty.buffer.ByteBufAllocator.DEFAULT.getClass().getName());

        EventLoopGroup group = new NioEventLoopGroup(1);
        Channel serverChannel = null;
        Channel acceptedChannel = null;
        Channel clientChannel = null;
        Throwable primary = null;
        boolean terminated = false;
        try {
            System.out.println("nioEventLoopGroup.size=1 executor=" + group.next().getClass().getName());
            Future<String> loopProbe = group.next().submit(() -> "event-loop-ok");
            String loopResult = loopProbe.get(TIMEOUT_SECONDS, TimeUnit.SECONDS);
            if (!"event-loop-ok".equals(loopResult)) {
                throw new AssertionError("event loop returned unexpected value: " + loopResult);
            }

            CountDownLatch roundTrip = new CountDownLatch(1);
            AtomicReference<Throwable> handlerFailure = new AtomicReference<>();
            AtomicReference<String> reply = new AtomicReference<>();
            AtomicReference<Channel> accepted = new AtomicReference<>();
            LocalAddress address = new LocalAddress("netty-local-channel-probe-" + System.nanoTime());

            ServerBootstrap server = new ServerBootstrap()
                    .group(group)
                    .channel(LocalServerChannel.class)
                    .childHandler(new ChannelInitializer<LocalChannel>() {
                        @Override
                        protected void initChannel(LocalChannel channel) {
                            accepted.set(channel);
                            channel.pipeline().addLast(new ChannelInboundHandlerAdapter() {
                                @Override
                                public void channelRead(ChannelHandlerContext ctx, Object msg) {
                                    try {
                                        if (!(msg instanceof ByteBuf)) {
                                            handlerFailure.compareAndSet(null, new AssertionError(
                                                    "server received non-ByteBuf: " + msg.getClass().getName()));
                                            ctx.writeAndFlush(Unpooled.copiedBuffer("ERROR",
                                                    StandardCharsets.UTF_8));
                                            return;
                                        }
                                        String received = ((ByteBuf) msg).toString(StandardCharsets.UTF_8);
                                        if (!PAYLOAD.equals(received)) {
                                            handlerFailure.compareAndSet(null, new AssertionError(
                                                    "server received unexpected payload: " + received));
                                            ctx.writeAndFlush(Unpooled.copiedBuffer("ERROR",
                                                    StandardCharsets.UTF_8));
                                            return;
                                        }
                                        ctx.writeAndFlush(Unpooled.copiedBuffer("ACK:" + received,
                                                StandardCharsets.UTF_8));
                                    } finally {
                                        releaseMessage(msg, handlerFailure);
                                    }
                                }

                                @Override
                                public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
                                    handlerFailure.compareAndSet(null, cause);
                                    ctx.close();
                                }
                            });
                        }
                    });

            ChannelFuture bindFuture = server.bind(address);
            if (!bindFuture.await(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                throw new AssertionError("server bind timed out after " + TIMEOUT_SECONDS + "s");
            }
            if (!bindFuture.isSuccess()) {
                throw new AssertionError("server bind failed", bindFuture.cause());
            }
            serverChannel = bindFuture.channel();

            Bootstrap client = new Bootstrap()
                    .group(group)
                    .channel(LocalChannel.class)
                    .handler(new ChannelInitializer<LocalChannel>() {
                        @Override
                        protected void initChannel(LocalChannel channel) {
                            channel.pipeline().addLast(new ChannelInboundHandlerAdapter() {
                                @Override
                                public void channelRead(ChannelHandlerContext ctx, Object msg) {
                                    try {
                                        if (!(msg instanceof ByteBuf)) {
                                            handlerFailure.compareAndSet(null, new AssertionError(
                                                    "client received non-ByteBuf: " + msg.getClass().getName()));
                                            return;
                                        }
                                        reply.set(((ByteBuf) msg).toString(StandardCharsets.UTF_8));
                                        roundTrip.countDown();
                                    } finally {
                                        releaseMessage(msg, handlerFailure);
                                    }
                                }

                                @Override
                                public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
                                    handlerFailure.compareAndSet(null, cause);
                                    roundTrip.countDown();
                                    ctx.close();
                                }

                                @Override
                                public void channelInactive(ChannelHandlerContext ctx) {
                                    roundTrip.countDown();
                                    ctx.fireChannelInactive();
                                }
                            });
                        }
                    });

            ChannelFuture connectFuture = client.connect(address);
            if (!connectFuture.await(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                throw new AssertionError("client connect timed out after " + TIMEOUT_SECONDS + "s");
            }
            if (!connectFuture.isSuccess()) {
                throw new AssertionError("client connect failed", connectFuture.cause());
            }
            clientChannel = connectFuture.channel();

            ChannelFuture writeFuture = clientChannel.writeAndFlush(
                    Unpooled.copiedBuffer(PAYLOAD, StandardCharsets.UTF_8));
            if (!writeFuture.await(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                throw new AssertionError("client write timed out after " + TIMEOUT_SECONDS + "s");
            }
            if (!writeFuture.isSuccess()) {
                throw new AssertionError("client write failed", writeFuture.cause());
            }

            if (!roundTrip.await(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                throw new AssertionError("local channel round trip timed out after "
                        + TIMEOUT_SECONDS + "s");
            }
            Throwable failure = handlerFailure.get();
            if (failure != null) {
                throw new AssertionError("local channel handler failed", failure);
            }
            String actualReply = reply.get();
            if (!("ACK:" + PAYLOAD).equals(actualReply)) {
                throw new AssertionError("local channel reply mismatch: " + actualReply);
            }
            System.out.println("localChannel.roundTrip=PASS payload=" + PAYLOAD);
            acceptedChannel = accepted.get();
        } catch (Throwable failure) {
            primary = failure;
            throw failure;
        } finally {
            closeQuietly(clientChannel);
            closeQuietly(acceptedChannel);
            closeQuietly(serverChannel);
            Future<?> shutdown = group.shutdownGracefully(SHUTDOWN_QUIET_PERIOD_SECONDS,
                    SHUTDOWN_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            terminated = shutdown.await(TIMEOUT_SECONDS, TimeUnit.SECONDS) && group.isTerminated();
            System.out.println("nioEventLoopGroup.terminated=" + terminated);
            if (!terminated) {
                AssertionError error = new AssertionError("NioEventLoopGroup did not terminate within "
                        + TIMEOUT_SECONDS + "s");
                if (primary == null) {
                    throw error;
                }
                primary.addSuppressed(error);
            }
        }
    }

    private static void checkMpscQueue(String label, Queue<Integer> queue, int count) {
        for (int i = 0; i < count; i++) {
            if (!queue.offer(i)) {
                throw new AssertionError(label + " MPSC queue rejected offer " + i);
            }
        }
        for (int i = 0; i < count; i++) {
            Integer value = queue.poll();
            if (value == null || value != i) {
                throw new AssertionError(label + " MPSC queue order mismatch at " + i + ": " + value);
            }
        }
        if (queue.poll() != null) {
            throw new AssertionError(label + " MPSC queue produced an extra element");
        }
        System.out.println("mpscQueue." + label + "=" + queue.getClass().getName());
    }

    private static void releaseMessage(Object msg, AtomicReference<Throwable> failure) {
        if (msg instanceof ByteBuf) {
            ByteBuf buffer = (ByteBuf) msg;
            boolean released = ReferenceCountUtil.release(buffer);
            if (!released || buffer.refCnt() != 0) {
                failure.compareAndSet(null, new AssertionError(
                        "ByteBuf was not fully released, refCnt=" + buffer.refCnt()));
            }
        } else {
            ReferenceCountUtil.release(msg);
        }
    }

    private static void closeQuietly(Channel channel) {
        if (channel == null) {
            return;
        }
        try {
            ChannelFuture closeFuture = channel.close();
            if (!closeFuture.await(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                System.err.println("channel close timed out: " + channel);
            }
        } catch (Throwable failure) {
            System.err.println("channel close failed: " + failure);
        }
    }
}
