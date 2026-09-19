package com.p2p.filetransfer.protocol

import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 二进制协议写入工具。
 * 所有多字节整数均为大端序（Big-Endian）。
 */
class ProtoWriter(private val out: OutputStream) {

    fun writeByte(value: Int) {
        out.write(value and 0xFF)
    }

    /** 4 字节大端 Int32 */
    fun writeInt32(value: Int) {
        out.write(ByteBuffer.allocate(4).order(ByteOrder.BIG_ENDIAN).putInt(value).array())
    }

    /** 8 字节大端 Int64 */
    fun writeInt64(value: Long) {
        out.write(ByteBuffer.allocate(8).order(ByteOrder.BIG_ENDIAN).putLong(value).array())
    }

    /** 8 字节 IEEE 754 double，大端 */
    fun writeDouble(value: Double) {
        out.write(ByteBuffer.allocate(8).order(ByteOrder.BIG_ENDIAN).putDouble(value).array())
    }

    /** 原始字节 */
    fun writeBytes(data: ByteArray) {
        out.write(data)
    }

    /** 写入 UTF-8 字符串（不带长度前缀） */
    fun writeString(value: String) {
        out.write(value.toByteArray(Charsets.UTF_8))
    }

    /** 写入长度前缀 + UTF-8 字符串（4 字节长度 + N 字节） */
    fun writeLengthPrefixedString(value: String) {
        val bytes = value.toByteArray(Charsets.UTF_8)
        writeInt32(bytes.size)
        out.write(bytes)
    }

    fun flush() {
        out.flush()
    }
}
