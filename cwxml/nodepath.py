from .element import (
    AttributeProperty,
    ElementTree,
    ListProperty,
    TextProperty,
    ValueProperty,
    VectorProperty
)


class YND:
    file_extension = ".ynd.xml"

    @staticmethod
    def from_xml_file(filepath):
        return NodeDictionary.from_xml_file(filepath)

    @staticmethod
    def write_xml(node_dict, filepath):
        return node_dict.write_xml(filepath)


class Link(ElementTree):
    tag_name = "Item"

    def __init__(self):
        super().__init__()
        self.to_area_id = ValueProperty("ToAreaID", 0)
        self.to_node_id = ValueProperty("ToNodeID", 0)
        self.flags0 = ValueProperty("Flags0", 0)
        self.flags1 = ValueProperty("Flags1", 0)
        self.flags2 = ValueProperty("Flags2", 0)
        self.link_length = ValueProperty("LinkLength", 0)


class LinkList(ListProperty):
    list_type = Link
    tag_name = "Links"


class JunctionHeightmap(ElementTree):
    tag_name = "Heightmap"

    def __init__(self):
        super().__init__()
    
    @classmethod
    def from_xml(cls, element):
        new = cls()
        if new.tag_name != element.tag:
            new.tag_name = element.tag
        
        # Store the hex heightmap data
        # Parse hex bytes from text like "00 01 02 FF"
        if element.text:
            hex_text = element.text.strip()
            hex_values = hex_text.split()
            new.data = bytes([int(h, 16) for h in hex_values if h])
        else:
            new.data = b""
        
        return new
    
    def to_xml(self):
        element = super().to_xml()
        if hasattr(self, 'data') and self.data:
            # convert bytes to hex string format
            hex_strings = [f"{b:02X}" for b in self.data]
            # format as rows
            element.text = "\n    " + " ".join(hex_strings) + "\n   "
        return element


class Junction(ElementTree):
    tag_name = "Item"

    def __init__(self):
        super().__init__()
        self.position = VectorProperty("Position")
        self.min_z = ValueProperty("MinZ", 0.0)
        self.max_z = ValueProperty("MaxZ", 0.0)
        self.size_x = ValueProperty("SizeX", 0)
        self.size_y = ValueProperty("SizeY", 0)
        self.heightmap = JunctionHeightmap()


class JunctionList(ListProperty):
    list_type = Junction
    tag_name = "Junctions"


class JunctionRef(ElementTree):
    tag_name = "Item"

    def __init__(self):
        super().__init__()
        self.area_id = ValueProperty("AreaID", 0)
        self.node_id = ValueProperty("NodeID", 0)
        self.junction_id = ValueProperty("JunctionID", 0)
        self.unk0 = ValueProperty("Unk0", 0)


class JunctionRefList(ListProperty):
    list_type = JunctionRef
    tag_name = "JunctionRefs"


class Node(ElementTree):
    tag_name = "Item"

    def __init__(self):
        super().__init__()
        self.area_id = ValueProperty("AreaID", 0)
        self.node_id = ValueProperty("NodeID", 0)
        self.street_name = TextProperty("StreetName", "")
        self.position = VectorProperty("Position")
        self.flags0 = ValueProperty("Flags0", 0)
        self.flags1 = ValueProperty("Flags1", 0)
        self.flags2 = ValueProperty("Flags2", 0)
        self.flags3 = ValueProperty("Flags3", 0)
        self.flags4 = ValueProperty("Flags4", 0)
        self.flags5 = ValueProperty("Flags5", 0)
        self.links = LinkList()


class NodeList(ListProperty):
    list_type = Node
    tag_name = "Nodes"


class NodeDictionary(ElementTree):
    tag_name = "NodeDictionary"

    def __init__(self):
        super().__init__()
        self.vehicle_node_count = ValueProperty("VehicleNodeCount", 0)
        self.ped_node_count = ValueProperty("PedNodeCount", 0)
        self.nodes = NodeList()
        self.junctions = JunctionList()
        self.junction_refs = JunctionRefList()
