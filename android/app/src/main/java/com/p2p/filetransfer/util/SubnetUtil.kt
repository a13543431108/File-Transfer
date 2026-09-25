package com.p2p.filetransfer.util

/** 子网推断工具（与桌面端 Python 逻辑保持一致） */
object SubnetUtil {

    /**
     * 根据 IPv4 地址推断扫描子网 CIDR。
     * - 169.254.x.x -> /16
     * - 10.x.x.x    -> /24（避免 /8 扫描 1600 万 IP）
     * - 172.16-31.x -> /12
     * - 其他        -> /24
     * IPv6 返回 null（暂不支持子网扫描）
     */
    fun subnetForIp(ip: String): String? {
        if (ip.contains(':')) return null
        val parts = ip.split('.')
        if (parts.size != 4) return null
        return try {
            when {
                ip.startsWith("169.254") -> ip + "/16"
                ip.startsWith("10.") -> ip + "/24"
                ip.startsWith("172.") -> {
                    val second = parts[1].toInt()
                    // 用 /16 而非 /12：企业网/校园网常用 /21 /22 或 /16，
                    // /12 覆盖 1048576 个 IP 会被 MAX_SCAN_IPS 截断到 65536
                    // （只覆盖 172.16.0.0-172.16.255.255），反而扫不到真实网段
                    // （如 172.20.93.x）。/16 正好 65536 个 IP，不触发截断。
                    if (second in 16..31) ip + "/16" else ip + "/24"
                }
                else -> ip + "/24"
            }
        } catch (_: NumberFormatException) {
            null
        }
    }

    /** 返回去重后的本机所有子网 CIDR 列表 */
    fun allSubnets(): List<String> {
        val set = LinkedHashSet<String>()
        for (ip in NetworkUtil.getLocalIPv4List()) {
            subnetForIp(ip)?.let { set.add(it) }
        }
        return set.toList()
    }

    /**
     * 计算 [cidr] 网段中可扫描的主机 IP 列表。
     * 对 /24 返回 1..254；对更大网段做截断（最多 [maxIps] 个）。
     */
    fun hostsForCidr(cidr: String, maxIps: Int): List<String> {
        val slash = cidr.indexOf('/')
        if (slash <= 0) return emptyList()
        val baseIp = cidr.substring(0, slash)
        val prefix = cidr.substring(slash + 1).toIntOrNull() ?: return emptyList()
        val parts = baseIp.split('.').mapNotNull { it.toIntOrNull() }
        if (parts.size != 4) return emptyList()

        val baseInt = (parts[0].toLong() shl 24) or
                (parts[1].toLong() shl 16) or
                (parts[2].toLong() shl 8) or
                parts[3].toLong()

        val hostBits = 32 - prefix
        if (hostBits < 0 || hostBits > 32) return emptyList()
        val size = 1L shl hostBits
        if (size <= 2) return emptyList()

        val networkInt = baseInt and (-1L shl hostBits)
        val first = networkInt + 1
        val last = networkInt + size - 2

        val result = ArrayList<String>()
        var cur = first
        while (cur <= last && result.size < maxIps) {
            result.add(longToIp(cur))
            cur++
        }
        return result
    }

    private fun longToIp(value: Long): String {
        val a = (value shr 24) and 0xFF
        val b = (value shr 16) and 0xFF
        val c = (value shr 8) and 0xFF
        val d = value and 0xFF
        return a.toString() + "." + b.toString() + "." + c.toString() + "." + d.toString()
    }
}
