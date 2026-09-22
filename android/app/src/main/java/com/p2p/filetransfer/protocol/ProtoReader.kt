package com.p2p.filetransfer.protocol

import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 二进制协议读取工具。
 * 所有多字节整数均为大端序（Big-Endian）。
 */
class ProtoReader(private val input: InputStream) {

    /** 精确读取 [size] 字节，不足则抛 IOException */
    fun readExact(size: Int): ByteArray {
        if (size <= 0) return ByteArray(0)
        val buf = ByteArray(size)
        var offset = 0
        while (offset < size) {
            val n = input.read(buf, offset, size - offset)
            if (n < 0) throw EOFException("连接中断")
            offset += n
        }
        return buf
    }

    fun readByte(): Int {
        val b = input.read()
        if (b < 0) throw EOFException("连接中断")
        return b
    }

    fun readInt32(): Int {
        return ByteBuffer.wrap(readExact(4)).order(ByteOrder.BIG_ENDIAN).int
    }

    fun readInt64(): Long {
        return ByteBuffer.wrap(readExact(8)).order(ByteOrder.BIG_ENDIAN).long
    }

    fun readDouble(): Double {
        return ByteBuffer.wrap(readExact(8)).order(ByteOrder.BIG_ENDIAN).double
    }

    /** 读取 4 字节长度 + N 字节 UTF-8 字符串 */
    fun readLengthPrefixedString(): String {
        val len = readInt32()
        if (len < 0 || len > 1024 * 1024) throw IOException("非法字符串长度: $len")
        return String(readExact(len), Charsets.UTF_8)
    }

    /** 读取固定长度 ASCII（如 "OK" / "MATCH     "），去除尾部空格 */
    fun readFixedAscii(size: Int): String {
        return String(readExact(size), Charsets.US_ASCII).trimEnd()
    }
}
