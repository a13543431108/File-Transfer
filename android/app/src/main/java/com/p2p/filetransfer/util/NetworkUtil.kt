package com.p2p.filetransfer.util

import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketException

/** 本机网络信息获取工具 */
object NetworkUtil {

    /** 获取本机所有非回环 IPv4 地址 */
    fun getLocalIPv4List(): List<String> {
        val result = mutableListOf<String>()
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return result
            for (iface in ifaces) {
                if (!iface.isUp || iface.isLoopback) continue
                for (addr in iface.inetAddresses) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        val host = addr.hostAddress ?: continue
                        if (!host.startsWith("169.254.") || result.isEmpty()) {
                            result.add(host)
                        }
                    }
                }
            }
        } catch (_: SocketException) {
        }
        return result
    }

    /** 获取本机所有非回环 IPv6 地址 */
    fun getLocalIPv6List(): List<String> {
        val result = mutableListOf<String>()
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return result
            for (iface in ifaces) {
                if (!iface.isUp || iface.isLoopback) continue
                for (addr in iface.inetAddresses) {
                    if (addr is Inet6Address && !addr.isLoopbackAddress) {
                        val host = addr.hostAddress ?: continue
                        val pure = host.substringBefore('%')
                        if (!pure.equals("::1", ignoreCase = true)) {
                            result.add(pure)
                        }
                    }
                }
            }
        } catch (_: SocketException) {
        }
        return result
    }

    /** 主 IPv4（第一个非回环地址），无则返回 null */
    fun primaryIPv4(): String? = getLocalIPv4List().firstOrNull()

    /** 本机主机名 */
    fun hostname(): String {
        return try {
            InetAddress.getLocalHost().hostName ?: "Android"
        } catch (_: Exception) {
            "Android"
        }
    }
}
