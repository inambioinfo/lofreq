#!/usr/bin/env python
"""
Helper functions for samtools' m/pileup
"""

#--- standard library imports
#
import subprocess
import logging
import re
import sys
import os

#--- third-party imports
#
# /

#--- project specific imports
#
# /


__author__ = "Andreas Wilm"
__version__ = "0.1"
__email__ = "wilma@gis.a-star.edu.sg"
__copyright__ = ""
__license__ = ""
__credits__ = [""]
__status__ = ""


#global logger
# http://docs.python.org/library/logging.html
LOG = logging.getLogger("")
logging.basicConfig(level=logging.WARN,
                    format='%(levelname)s [%(asctime)s]: %(message)s')


        
    
class PileupColumn():
    """
    Pileup column class. Parses samtools m/pileup output.

    See http://samtools.sourceforge.net/pileup.shtml

    Using namedtuples seemed too inflexible:
    namedtuple('PileupColumn', 'chrom coord refbase coverage read_bases base_quals')
    pileup_column = PileupColumn(*(line.split('\t')))
    pileup_column = PileupColumn._make((line.split('\t')))

    NOTE: this class ignores indels
    """

    # FIXME add quality_filter option

    def __init__(self, line=None):
        """
        """
        # chromosome name
        self.chrom = None
        
        # 0-based coordinate (in: 1-based coordinate)
        self.coord = None
        
        # reference base
        # FIXME: seems to be N always even if ref file is given (-f)
        self.ref_base = None
        
        # the number of reads covering the site
        self.coverage = None
        
        # unprocessed read bases
        self.read_bases_raw = None
        
        # the above without markup (mixed case)
        self.read_bases = None
        
        # unprocessed base qualities string
        self.base_quals_raw = None
        
        # the above but as list of phred scores (ints)
        self.base_quals = None
        
        if line:
            self.parse_line(line)

        
    def parse_line(self, line, keep_strand_info=False, delete_raw_values=True):
        """
        Split a line of pileup output and set values accordingly

        From http://samtools.sourceforge.net/pileup.shtml:
        
        At the read base column, a dot stands for a match to the
        reference base on the forward strand, a comma for a match on
        the reverse strand, `ACGTN' for a mismatch on the forward
        strand and `acgtn' for a mismatch on the reverse strand. A
        pattern `\+[0-9]+[ACGTNacgtn]+' indicates there is an
        insertion between this reference position and the next
        reference position. The length of the insertion is given by
        the integer in the pattern, followed by the inserted sequence.
        Here is an example of 2bp insertions on three reads:

        seq2 156 A 11  .$......+2AG.+2AG.+2AGGG    <975;:<<<<<


        Similarly, a pattern `-[0-9]+[ACGTNacgtn]+' represents a
        deletion from the reference. Here is an exmaple of a 4bp
        deletions from the reference, supported by two reads:

        seq3 200 A 20 ,,,,,..,.-4CACC.-4CACC....,.,,.^~. ==<<<<<<<<<<<::<;2<<

        Also at the read base column, a symbol `^' marks the start of
        a read segment which is a contiguous subsequence on the read
        separated by `N/S/H' CIGAR operations. The ASCII of the
        character following `^' minus 33 gives the mapping quality. A
        symbol `$' marks the end of a read segment. Start and end
        markers of a read are largely inspired by Phil Green's CALF
        format. These markers make it possible to reconstruct the read
        sequences from pileup. SAMtools can optionally append mapping
        qualities to each line of the output. This makes the output
        much larger, but is necessary when a subset of sites are
        selected.
        """

        line_split = line.split('\t')
        assert len(line_split) == 6, (
            "Couldn't parse pileup line: '%s'" % line)

        self.chrom = line_split[0]
        # in: 1-based coordinate
        self.coord = int(line_split[1]) - 1
        self.ref_base = line_split[2].upper() # paranoia upper()
        self.coverage = int(line_split[3])
        self.read_bases_raw = line_split[4]
        # self.read_bases created below
        self.base_quals_raw = line_split[5]
        # self.base_quals created below

        # Compute a Phred scale version of base_quals_raw
        self.base_quals =  [ord(c)-33 for c in self.base_quals_raw]

        # Create a clean version of read_bases_raw
        self.read_bases = self.read_bases_raw
        self.read_bases = self.rem_startend_markup(self.read_bases)
        if keep_strand_info:
            self.read_bases = self.read_bases.replace(".", self.ref_base.upper())
            self.read_bases = self.read_bases.replace(",", self.ref_base.lower())
        else:
            self.read_bases = self.read_bases.replace(".", self.ref_base)
            self.read_bases = self.read_bases.replace(",", self.ref_base)
            self.read_bases = self.read_bases.upper()

        # note: deletion on reference ('*') have qualities which will
        # be deleted as well
        (self.read_bases, self.base_quals) = self.rem_indel_markup(
            self.read_bases, self.base_quals)

        assert len(self.read_bases) == len(self.base_quals), (
            "Mismatch between number of parsed bases and quality values at %s:%d\n"
            % (self.chrom, self.coord+1))

        #if self.coord == 52-1:
        #    import pdb; pdb.set_trace()
            
        if delete_raw_values:
            self.read_bases_raw = None
            self.base_quals_raw = None
            

    @staticmethod
    def rem_startend_markup(read_bases_str):
        """
        Remove end and start (incl mapping) markup from read bases string

        From http://samtools.sourceforge.net/pileup.shtml:

        ...at the read base column, a symbol `^' marks the start of a
        read segment which is a contiguous subsequence on the read
        separated by `N/S/H' CIGAR operations. The ASCII of the
        character following `^' minus 33 gives the mapping quality. A
        symbol `$' marks the end of a read segment.
        """

        return re.sub('(\^.|\$)', '', read_bases_str)

    
    @staticmethod
    def rem_indel_markup(bases, base_quals):
        """
        Remove indel markup from read bases string

        From http://samtools.sourceforge.net/pileup.shtml:

        A pattern '\+[0-9]+[ACGTNacgtn]+' indicates there is an
        insertion between this reference position and the next
        reference position. Similarly, a pattern
        '-[0-9]+[ACGTNacgtn]+' represents a deletion from the
        reference. The deleted bases will be presented as '*' in the
        following lines.
        """
       
        # First the initial +- markup for which no quality value
        # exists: find out how many insertion/deletions happened
        # first, so that you can then delete the right amount of
        # nucleotides afterwards.
        #
        while True:
            match = re.search('[-+][0-9]+', bases)
            if not match:
                break
            num = int(bases[match.start()+1:match.end()])
            left = bases[:match.start()]
            right = bases[match.end()+num:]
            bases = left + right

        # now delete the deletion on the reference marked as stars
        # (which have quality values; see also
        # http://seqanswers.com/forums/showthread.php?t=3388)
        # and return
        base_quals = [(q) for (b, q) in zip(bases, base_quals)
                      if b != '*']
        bases = ''.join([b for b in bases if b != '*'])
        
        return (bases, base_quals)


    def rem_bases_below_qual(self, ign_bases_below_q=3):
        """Illumina 1.5+ indicates errors with Q2 (or lower). Those
        bases should not be used for downstream analysis. Therefore we
        use 3 as default cutoff. Also note that GATK did not use to
        recalibrate bases Q<5.
        """
        
        bases_and_quals = [(b, q)
                           for (b, q) in zip(self.read_bases, self.base_quals)
                           if q >= ign_bases_below_q]
        #rem_bases = len(self.read_bases) - len(bases_and_quals)
        #LOG.critical("rem_bases_below_qual %d: Removed %d bases from %s:%d" % (
        #    ign_bases_below_q, rem_bases, self.chrom, self.coord+1))
        self.read_bases = ''.join([b for (b, q) in bases_and_quals])
        self.base_quals =  [q for (b, q) in bases_and_quals]

        
    def rem_ambiguities(self, allowed_bases = 'ACGT'):
        """Remove bases and their qualities if not in allowed_bases
        """
        
        bases_and_quals = [(b, q)
                           for(b, q) in zip(self.read_bases, self.base_quals)
                           if b in allowed_bases]
        #rem_bases = len(self.read_bases) - len(bases_and_quals)
        #LOG.debug("rem_ambiguities: Removed %d bases from %s:%d" % (
        #    rem_bases, self.chrom, self.coord+1))
        self.read_bases = ''.join([b for (b, q) in bases_and_quals])
        self.base_quals =  [q for (b, q) in bases_and_quals]
        
        

def pileup_column_generator(fbam, seq, fref=None, start_pos=None, end_pos=None):
    """
    Generates pileup colums from BAM files. Uses mpileup and disables
    all filtering.

    FIXME: mpileup is the generator function
    """

    # 'samtools pileup' swallows some reads. Need to use mpileup
    # instead *and* increase max sample depth (-d 1000000). BAQ
    # computation can be influenced with -B and -E.
    
    mpileup_args = ['-d', ' 1000000']
    # used to contain '-Q 0', but that only affects variant calling
    
    #import pdb; pdb.set_trace()
    pileupcolumns = mpileup(fbam, seq, mpileup_args, fref, start_pos, end_pos)
    if not pileupcolumns:
        LOG.fatal("mpileup on '%s' failed (used args: %s)...Exiting" % (
            fbam, mpileup_args))
        sys.exit(1)
    LOG.debug("Successfully piled-up '%s'." % (fbam))

    return pileupcolumns

        

def mpileup(fbam, seq, extra_args, fref=None, start_pos=None, end_pos=None, samtools_binary="samtools"):
    """
    Generator! Calls 'samtools mpileup', parse output and returns the
    pileup columns

    Arguments:
    - fbam:
    is the bam file to parse
    - seq:
    The seq/chromosome to use
    - extra_args:
    is a string of extra arguments passed down to samtools mpileup.
    - fref:
    Path to index reference sequence file
    - start_pos:
    Start position for pileup. Directly handed down to mpileup so unit-offset
    - end_pos:
    End position for pileup. Directly handed down to mpileup so unit-offset
    - samtools_binary:
    samtools binary name/path
    
    Results:
    Returns pileup columns as  list of PileupColumn instances
    
    NOTE:
    Results might change or exeuction fail depending on used samtools version.
    Tested on Version: 0.1.13 (r926:134)

    This should be replaced once pysam support mpileup
    """

    bam_header = header(fbam)
    step = 1000

    assert any([seq in line for line in bam_header]), (
        "Couldn't find seq '%s' in header of '%s'" % (
        seq, fbam))
    
    if not start_pos:
        # samtool binary uses unit-offset coordinates so start at 1    
        start_pos = 1
    cur_pos = start_pos        
    if not end_pos:
        if bam_header == False:
            LOG.critical("samtools header parsing failed test")
            raise ValueError
        end_pos = len_for_sq(bam_header, seq)
    assert '-r ' not in extra_args, (
        "Was planning to use -r myself but you requested its use again in extra argument")
    assert '-l ' not in extra_args, (
        "Will use -r and am thus not sure if requested us of -l is allowed at the same time. Please test first")
    if fref:
        assert os.path.exists(fref), (
            "Reference file '%s' does not exist" % fref)
        assert '-f ' not in extra_args, (
            "Already got a reference as argument, but you want to use it again with -f")

    last_coord_seen = None
    while cur_pos <= end_pos:
        region_start = cur_pos
        region_end = cur_pos+step-1
        if region_end>end_pos:
            region_end = end_pos
        region_arg = "%s:%d-%d" % (seq, region_start, region_end)
        cmd =  [samtools_binary, 'mpileup', '-r', region_arg]
        if fref:
            cmd.append('-f')
            cmd.append(fref)
        cmd.extend(extra_args)
        cmd.append(fbam)
        LOG.debug("calling: %s" % (' '.join(cmd)))
        #import pdb; pdb.set_trace()
        process = subprocess.Popen(cmd,
                                   shell=False,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        (stdoutdata, stderrdata) =  process.communicate()
     
        retcode = process.returncode
        if retcode != 0:
            LOG.fatal("%s exited with error code '%d'." \
                      " Command was '%s'. stderr was: '%s'" % (
                          cmd[0], retcode, ' '.join(cmd), stderrdata))
            raise OSError #StopIteration

                           
        for line in stderrdata.split("\n"):#[:-1]:# ignore empty last element
            if not len(line):
                continue
            if line == "[mpileup] 1 samples in 1 input files":
                continue
            elif line == "[fai_load] build FASTA index.":
                continue
            else:
                LOG.warn("Unhandled line on stderr detected: %s" % (line))

        for line in stdoutdata.split("\n"):
            # last element is an empty new line
            if len(line)==0:
                continue

            pileupcolumn = PileupColumn(line)

            yield pileupcolumn

        cur_pos += step
    #LOG.critical("DEBUG reached the end")



def sq_from_header(header):
    """
    Parse sequence name/s from header. Will return a list. Not sure if
    several names are allowed.
    """

    sq_list = []
    for line in header:
        line_split = line.split()
        try:
            if line_split[0] != "@SQ":
                continue
            if line_split[1].startswith("SN:"):
                sq_list.append(line_split[1][3:])
        except IndexError:
            continue
    return sq_list



def len_for_sq(header, sq):
    """
    Parse sequence length from header.
    """

    for line in header:
        line_split = line.split('\t')
        try:
            if line_split[0] != "@SQ":
                continue
            if not line_split[1].startswith("SN:"):
                continue
            if not line_split[1][3:] == sq:
                continue

            # right line, but which is the right field?
            for field in line_split[2:]:
                if field.startswith("LN:"):
                    return int(field[3:])
        except IndexError:
            continue
    return None

        
            
    
def header(fbam, samtools_binary="samtools"):
    """
    Calls 'samtools -H view', parse output and return
    
    Arguments:
    - fbam:
    is the bam file to parse
    - samtools_binary:
    samtools binary name/path
    
    Results:
    Returns the raw header as list of lines
    
    NOTE:
    Results might change or exeuction fail depending on used samtools version.
    Tested on Version: 0.1.13 (r926:134)
    """
    
    cmd = '%s view -H %s' % (
        samtools_binary, fbam)

    # http://samtools.sourceforge.net/pileup.shtml
    LOG.debug("calling: %s" % (cmd))
    # WARNING: "The data read is buffered in memory, so do not use
    # this method if the data size is large or unlimited." Only other
    # option I see is to write to file.
    process = subprocess.Popen(cmd.split(),
                               shell=False,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    (stdoutdata, stderrdata) =  process.communicate()

    retcode = process.returncode
    if retcode != 0:
        LOG.fatal("%s exited with error code '%d'." \
                  " Command was '%s'. stderr was: '%s'" % (
                      cmd.split()[0], retcode, cmd, stderrdata))
        raise OSError
                       
    for line in stderrdata.split("\n"):
        if not len(line):
            continue
        if line == "[mpileup] 1 samples in 1 input files":
            continue
        if line == "[fai_load] build FASTA index.":
            continue
        else:
            LOG.warn("Unhandled line on stderr detected: %s" % (line))

    return stdoutdata.split("\n")
        
