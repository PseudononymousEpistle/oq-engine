/*
    Copyright (c) 2010-2012, GEM Foundation.

    OpenQuake is free software: you can redistribute it and/or modify it
    under the terms of the GNU Affero General Public License as published
    by the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    OpenQuake is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with OpenQuake.  If not, see <http://www.gnu.org/licenses/>.
*/

package org.gem.engine.hazard.parsers;

import java.io.File;
import java.util.HashMap;
import java.util.Map;
import java.util.StringTokenizer;

import org.dom4j.Document;
import org.dom4j.XPath;
import org.dom4j.io.SAXReader;
import org.dom4j.xpath.DefaultXPath;
import org.opensha.commons.geo.Location;
import org.opensha.sha.earthquake.EqkRupture;
import org.opensha.sha.faultSurface.ApproxEvenlyGriddedSurface;
import org.opensha.sha.faultSurface.FaultTrace;
import org.opensha.sha.faultSurface.PointSurface;
import org.opensha.sha.faultSurface.StirlingGriddedSurface;
import org.opensha.sha.util.TectonicRegionType;

/**
 * Reads NRML definitions of rupture elements.
 */
public class RuptureReader
{

    private final File file;
    private Document document;
    private final double gridSpacing;

    private static final Map<String, String> namespaces = new HashMap<String, String>();

    {
        namespaces.put("gml", "http://www.opengis.net/gml");
        namespaces.put("nrml", "http://openquake.org/xmlns/nrml/0.3");
    }

    static class InvalidFormatException extends RuntimeException
    {

        private static final long serialVersionUID = 4430401704068185529L;

        public InvalidFormatException(String message)
        {
            super(message);
        }

    }

    /**
     * Parses the document and updates the rupture object.
     */
    interface RuptureParser
    {

        /**
         * Updates the given rupture with the data found in the document.
         */
        void update(EqkRupture rupture);

    }

    class PointRuptureParser implements RuptureParser
    {

        private final Document document;

        PointRuptureParser(Document document)
        {
            this.document = document;
        }

        @Override
        public void update(EqkRupture rupture)
        {
            rupture.setHypocenterLocation(location());
            rupture.setAveRake(asDouble("//nrml:rake"));

            PointSurface surface = new PointSurface(location());
            surface.setAveStrike(asDouble("//nrml:strike"));
            surface.setAveDip(asDouble("//nrml:dip"));

            rupture.setRuptureSurface(surface);
        }

        private Location location()
        {
            String pos = xpath("//gml:pos").selectSingleNode(document).getText();
            StringTokenizer splitter = new StringTokenizer(pos);
            return nextLocation(splitter);
        }

    }

    class SimpleFaultRuptureParser implements RuptureParser
    {

        private final Document document;

        SimpleFaultRuptureParser(Document document)
        {
            this.document = document;
        }

        @Override
        public void update(EqkRupture rupture)
        {
            rupture.setAveRake(asDouble("//nrml:rake"));

            double dip = asDouble("//nrml:dip");
            double usd = asDouble("//nrml:upperSeismogenicDepth");
            double lsd = asDouble("//nrml:lowerSeismogenicDepth");

            StirlingGriddedSurface surface = new StirlingGriddedSurface(trace(), dip, usd, lsd, gridSpacing);
            rupture.setRuptureSurface(surface);
        }

        private FaultTrace trace()
        {
            FaultTrace trace = new FaultTrace("");
            String values = xpath("//gml:posList").selectSingleNode(document).getText();
            StringTokenizer splitter = new StringTokenizer(values);

            while (splitter.hasMoreTokens())
            {
                trace.add(nextLocation(splitter));
            }

            return trace;
        }

    }

    class ComplexFaultRuptureParser implements RuptureParser
    {

        private final Document document;

        ComplexFaultRuptureParser(Document document)
        {
            this.document = document;
        }

        @Override
        public void update(EqkRupture rupture)
        {
            rupture.setAveRake(asDouble("//nrml:rake"));

            String topFaultXPath = "//nrml:faultTopEdge/gml:LineString/gml:posList";
            String bottomFaultXPath = "//nrml:faultBottomEdge/gml:LineString/gml:posList";

            FaultTrace topFault = trace(topFaultXPath);
            FaultTrace bottomFault = trace(bottomFaultXPath);
            ApproxEvenlyGriddedSurface surface = new ApproxEvenlyGriddedSurface(topFault, bottomFault, gridSpacing);

            rupture.setRuptureSurface(surface);
        }

        private FaultTrace trace(String xpath)
        {
            FaultTrace trace = new FaultTrace(null);
            String values = xpath(xpath).selectSingleNode(document).getText();
            StringTokenizer splitter = new StringTokenizer(values);

            while (splitter.hasMoreTokens())
            {
                trace.add(nextLocation(splitter));
            }

            return trace;
        }

    }

    public RuptureReader(File file, double gridSpacing)
    {
        if (!file.isFile())
        {
            throw new IllegalArgumentException("unknown file!");
        }

        this.file = file;
        this.gridSpacing = gridSpacing;
    }

    public RuptureReader(String path, double gridSpacing)
    {
        this(new File(path), gridSpacing);
    }

    /**
     * Reads the document and returns the rupture object.
     *
     * @return the rupture defined in the document
     */
    public EqkRupture read()
    {
        SAXReader reader = new SAXReader();

        try
        {
            document = reader.read(this.file);
        }
        catch (Exception e)
        {
            throw new RuntimeException(e);
        }

        EqkRupture rupture = new EqkRupture();

        rupture.setMag(asDouble("//nrml:magnitude"));
        rupture.setTectRegType(tectonicRegionType());

        if (xpath("//nrml:pointRupture").selectSingleNode(document) != null)
        {
            new PointRuptureParser(document).update(rupture);
        }
        else if (xpath("//nrml:simpleFaultRupture").selectSingleNode(document) != null)
        {
            new SimpleFaultRuptureParser(document).update(rupture);
        }
        else if (xpath("//nrml:complexFaultRupture").selectSingleNode(document) != null)
        {
            new ComplexFaultRuptureParser(document).update(rupture);
        }
        else
            throw new RuntimeException("'" + file + "' isn't a known rupture type");

        return rupture;
    }

    private XPath xpath(String pattern)
    {
        XPath xpath = new DefaultXPath(pattern);
        xpath.setNamespaceURIs(namespaces);

        return xpath;
    }

    private TectonicRegionType tectonicRegionType()
    {
        String name = xpath("//nrml:tectonicRegion").selectSingleNode(document).getText();
        return TectonicRegionType.getTypeForName(name);
    }

    private double asDouble(String pattern)
    {
        return Double.parseDouble(xpath(pattern).selectSingleNode(document).getText());
    }

    private Location nextLocation(StringTokenizer splitter)
    {
        if (splitter.countTokens() % 3 != 0)
        {
            throw new InvalidFormatException("longitude, latitude and depth must be always specified!");
        }

        double lon = Double.parseDouble(splitter.nextToken());
        double lat = Double.parseDouble(splitter.nextToken());
        double depth = Double.parseDouble(splitter.nextToken());

        return new Location(lat, lon, depth);
    }

}
