"""Small structural media checks; these are not codec or content safety scans."""


def has_mp4_container_signature(content: bytes) -> bool:
    """Check top-level ISO BMFF boxes before accepting or previewing an MP4."""
    offset = 0
    box_types: set[bytes] = set()
    box_count = 0
    while offset < len(content):
        if offset + 8 > len(content) or box_count >= 10_000:
            return False
        box_size = int.from_bytes(content[offset:offset + 4], "big")
        box_type = content[offset + 4:offset + 8]
        header_size = 8
        if box_size == 1:
            if offset + 16 > len(content):
                return False
            box_size = int.from_bytes(content[offset + 8:offset + 16], "big")
            header_size = 16
        elif box_size == 0:
            box_size = len(content) - offset
        if box_type == b"uuid":
            header_size += 16
        if box_size < header_size or offset + box_size > len(content):
            return False
        if box_type in {b"ftyp", b"moov", b"mdat"} and box_size > header_size:
            box_types.add(box_type)
        if offset == 0 and box_type != b"ftyp":
            return False
        offset += box_size
        box_count += 1
    return offset == len(content) and {b"ftyp", b"moov", b"mdat"}.issubset(box_types)
