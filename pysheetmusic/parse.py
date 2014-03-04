import lxml.etree
import zipfile
from os.path import join, dirname
import os
from contextlib import contextmanager
from fractions import Fraction

from raygllib.utils import timeit

from . import sheet as S
from .utils import monad


class FormatError(Exception):
    pass

class ValidateError(Exception):
    pass

class ParseContext:
    def __init__(self):
        self.sheet = None
        self.page = None
        self.measure = None
        self.beams = {}

class MusicXMLParser:
    @staticmethod
    def get_schema():
        with _run_in_dir(join(dirname(__file__), 'schema')):
            with open('musicxml.xsd') as schemaFile:
                schemaDoc = lxml.etree.XML(schemaFile.read().encode('utf-8'))
                schema = lxml.etree.XMLSchema(schemaDoc)
        return schema

    def __init__(self):
        self.schema = self.get_schema()

    @timeit
    def parse(self, path):
        print('parsing:', os.path.split(path)[-1])
        xmlDoc = _read_musicxml(path)
        if not self.schema(xmlDoc):
            raise ValidateError(path, self.schema.error_log.filter_from_errors())
        context = ParseContext()
        context.sheet = S.Sheet(xmlDoc)
        context.page = context.sheet.new_page()
        # Support single part only.
        partNode = xmlDoc.find('part')
        handledTags = ('attributes', 'note', 'backup', 'forward', 'barline')
        handlers = {tag: getattr(self, 'handle_' + tag) for tag in handledTags}
        for measureNode in partNode.findall('measure'):
            context.measure = measure = S.Measure(measureNode)
            if measureNode.find('print[@new-page="yes"]') is not None:
                context.page = context.sheet.new_page()
            context.page.add_measure(measure)
            if measureNode.find('print') is None:
                measure.follow_prev_layout()
            else:
                self.handle_print(context, measureNode.find('print'))
            for child in measureNode.getchildren():
                # Types handled:
                #   note, backup, forward, attributes, print, barline
                # Types not handled:
                #   direction, harmony, figured-bass, bookmark,
                #   link, grouping, sound
                if child.tag in handlers:
                    handlers[child.tag](context, child)
            measure.finish()
        context.sheet.finish()
            # print(measure)
        # print(context.page)
        return context.sheet

    def handle_print(self, context, node):
        measure = context.measure
        page = context.page
        staffSpacing = node.attrib.get('staff-spacing', None)
        if staffSpacing is not None:
            measure.staffSpacing = float(staffSpacing)
        newSystem = measure.prev is None or \
            node.attrib.get('new-system', 'no').lower() == 'yes'
        newPage = measure.prev is None or\
            node.attrib.get('new-page', 'no').lower() == 'yes'
        # system layout
        systemMargins = S.Margins(node.find('system-layout/system-margins'))
        if newPage:
            measure.isNewSystem = True
            if node.find('page-layout') is not None:
                # TODO: Adjust page layout.
                pass
            topSystemDistance = float(
                node.find('system-layout/top-system-distance').text)
            measure.y = (page.size[1] - page.margins.top - topSystemDistance
                - measure.height)
            measure.x = systemMargins.left + page.margins.left
        elif newSystem:
            measure.isNewSystem = True
            measure.x = systemMargins.left + measure.page.margins.left
            measure.y = (measure.prev.y
                - float(node.find('system-layout/system-distance').text)
                - measure.height)
        else:
            measure.follow_prev_layout()
            measureDistance = node.find('measure-layout/measure-distance')
            if measureDistance:
                measure.x += float(measureDistance.text)

    def handle_attributes(self, context, node):
        measure = context.measure
        if node.find('divisions') is not None:
            measure.timeDivisions = int(node.find('divisions').text)
        # Clef
        if node.find('clef') is not None:
            measure.set_clef(S.Clef(node.find('clef')))
        # Time
        # Key

    # @profile
    def handle_note(self, context, node):
        grace = node.find('grace')
        cue = node.find('cue')
        measure = context.measure
        if grace is not None:
            pass #TODO
        elif cue is not None:
            pass #TODO
        else:
            isChord = node.find('chord') is not None
            duration = lambda: \
                Fraction(node.find('duration').text) / measure.timeDivisions / 4
            dots = lambda: [None] * len(node.xpath('dot'))
            type = lambda: monad(node.find('type'), lambda x:x.text, None)

            def pos():
                try:
                    return float(node.attrib['default-x']), float(node.attrib['default-y'])
                except (KeyError, ValueError):
                    return None

            if node.find('pitch') is not None:
                pitch = S.Pitch(node.find('pitch'))
                stem = monad(node.find('stem'), S.Stem, None) if not isChord else None
                accidental = monad(node.find('accidental'), S.Accidental, None)
                note = S.PitchedNote(
                    pos(), duration(), dots(), type(),
                    pitch, stem, accidental)
                beamNode = node.find('beam')
                if stem and beamNode is not None:
                    beamType = beamNode.text
                    number = beamNode.attrib['number']
                    if beamType == 'begin':
                        beam = S.Beam()
                        context.beams[number] = beam
                    elif beamType == 'continue':
                        beam = context.beams[number]
                    elif beamType == 'end':
                        beam = context.beams[number]
                        measure.add_beam(beam)
                    beam.stems.append(stem)
                measure.add_note(note, isChord)
            elif node.find('rest') is not None:
                note = S.Rest(pos(), duration(), dots(), type())
                measure.add_note(note, isChord)

    def handle_forward(self, context, node):
        measure = context.measure
        duration = Fraction(node.find('duration').text) / measure.timeDivisions / 4
        measure.change_time(duration)

    def handle_backup(self, context, node):
        measure = context.measure
        duration = -Fraction(node.find('duration').text) / measure.timeDivisions / 4
        measure.change_time(duration)

    def handle_barline(self, context, node):
        pass


def _read_musicxml(path):
    content = None
    try:
        zfile = zipfile.ZipFile(path)
        for name in zfile.namelist():
            if not name.startswith('META-INF/') and name.endswith('.xml'):
                content = zfile.read(name)
                break
    except zipfile.BadZipFile:
        with open(path, 'rb') as infile:
            content = infile.read()
    if not content:
        raise FormatError()
    try:
        return lxml.etree.XML(content)
    except lxml.etree.XMLSyntaxError:
        raise FormatError()

@contextmanager
def _run_in_dir(dest):
    curDir = os.path.abspath(os.curdir)
    os.chdir(dest)
    yield
    os.chdir(curDir)